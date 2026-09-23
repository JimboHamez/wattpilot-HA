"""Base entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from packaging.version import Version

from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, STATE_UNKNOWN, EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import CONF_CONNECTION, CONF_LOCAL, DEFAULT_NAME, DOMAIN
from .utils import GetChargerProp, async_GetChargerProp, async_SetChargerProp, property_update_signal

if TYPE_CHECKING:
    from wattpilot_api import Wattpilot

    from homeassistant.core import HomeAssistant

    from .models import WattpilotConfigEntry

_LOGGER: Final = logging.getLogger(__name__)


class ChargerPlatformEntity(Entity):
    """Base class shared by every Wattpilot platform entity.

    Entities are built from YAML definitions (see the per-platform ``*.yaml``
    catalogs), so this class is generic over three value ``source`` kinds:

    - ``property``     -> a key in ``charger.all_properties`` (push-capable).
    - ``attribute``    -> a plain attribute on the charger object (poll-only).
    - ``namespacelist`` -> an indexed ``SimpleNamespace`` inside a property.

    ``_state_attr`` names the attribute a subclass stores its state in
    (e.g. ``_attr_native_value`` for sensor/number), letting the shared
    update logic write state without knowing the concrete platform.

    ``_attr_has_entity_name`` is set so Home Assistant composes the visible
    name from the device name plus a translated entity name looked up via
    ``_attr_translation_key`` (see the ``entity`` section of ``strings.json``).
    """

    _state_attr = "state"
    _attr_has_entity_name = True
    # The catalog description is the same text on every state change; keep it
    # visible on the entity but out of the recorder's history.
    _unrecorded_attributes = frozenset({"description"})

    def __init__(
        self, hass: HomeAssistant, entry: WattpilotConfigEntry, entity_cfg: dict[str, Any], charger: Wattpilot
    ) -> None:
        """Initialize the object."""
        try:
            self._charger_id = str(entry.data.get(CONF_FRIENDLY_NAME, entry.data.get(CONF_IP_ADDRESS, DEFAULT_NAME)))
            self._source = entity_cfg.get("source", "property")
            # Only a namespacelist id carries its item index as a suffix
            # ('cards_0' -> property 'cards', item 0). Every other id is used
            # verbatim: attribute names contain underscores of their own
            # ('car_connected', 'access_state'), and splitting them would look
            # up a 'car' / 'access' attribute that does not exist.
            raw_id = str(entity_cfg.get("id"))
            self._identifier = raw_id.split("_", 1)[0] if self._source == "namespacelist" else raw_id
            _LOGGER.debug("%s - %s: __init__", self._charger_id, self._identifier)

            self._charger = charger
            self._namespace_id = int(entity_cfg.get("namespace_id", 0))
            self._default_state = entity_cfg.get("default_state")
            self._entity_cfg = entity_cfg

            self._entry = entry
            self.hass = hass

            self._init_failed = True
            self._fw_supported = self._check_firmware_supported()
            if self._fw_supported is not True:
                return None
            self._variant_supported = self._check_variant_supported()
            if self._variant_supported is not True:
                return None
            self._connection_supported = self._check_connection_supported()
            if self._connection_supported is not True:
                return None

            self._init_failed = False
            # A charger only reports the properties its model and firmware
            # support (e.g. no 'ust'/'lck' on some Flex firmwares, fewer than ten
            # RFID card slots). The catalogs are deliberately a superset, so an
            # absent value means "skip this entity", not "something went wrong":
            # log it at debug, or every setup floods the log with errors.
            # Absence is judged on whether the charger reports the property at
            # all, not on its value - see _property_reported. A definition that
            # carries a default_state is never skipped: it has a value to show
            # regardless, which is the behaviour those definitions already had.
            if self._fw_supported is not False:
                if self._source == "attribute" and not hasattr(self._charger, self._identifier):
                    _LOGGER.debug(
                        "%s - %s: __init__: Charger does not have an attribute: %s (maybe a property?)",
                        self._charger_id,
                        self._identifier,
                        self._identifier,
                    )
                    self._init_failed = True
                elif self._source == "property" and self._default_state is None and not self._property_reported():
                    _LOGGER.debug(
                        "%s - %s: __init__: Charger does not have a property: %s (maybe an attribute?)",
                        self._charger_id,
                        self._identifier,
                        self._identifier,
                    )
                    self._init_failed = True
                elif self._source == "namespacelist" and self._get_namespacelist_item() is None:
                    _LOGGER.debug(
                        "%s - %s: __init__: Charger does not have a namespacelist item: %s[%s]",
                        self._charger_id,
                        self._identifier,
                        self._identifier,
                        self._namespace_id,
                    )
                    self._init_failed = True
            if self._init_failed is True:
                return None

            # Home Assistant resolves the visible name from the device name plus
            # the translated entity name; the translation key is derived from the
            # entity's uid (falling back to its id), matching the keys generated
            # into the 'entity' section of strings.json. Do not set _attr_name:
            # it would override the translated name.
            self._attr_translation_key = slugify(
                str(self._entity_cfg.get("uid", self._entity_cfg.get("id", self._identifier)))
            )
            # No _attr_icon: icons come from icons.json, looked up by the same
            # translation key (quality-scale rule icon-translations).
            self._attr_device_class = self._entity_cfg.get("device_class", None)
            self._entity_category = self._entity_cfg.get("entity_category", None)
            self._set_type = self._entity_cfg.get("set_type", None)

            self._attributes = {}
            self._attributes["description"] = self._entity_cfg.get("description", None)
            setattr(self, self._state_attr, self._entity_cfg.get("default_state", STATE_UNKNOWN))

            self._init_platform_specific()

            self._attr_unique_id = (
                self._charger_id + "-" + self._entity_cfg.get("uid", self._entity_cfg.get("id", self._identifier))
            )
            if self._init_failed is True:
                return None
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: __init__ failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return None

    async def _async_write_property(
        self, identifier: str, value: Any, *, force: bool = False, force_type: str | None = None
    ) -> None:
        """Write a value to the charger for an entity action, raising if it was not taken.

        Entity actions (turn on, press, select, set value, install) are started by a
        person or an automation, so a failed write has to reach them - the UI shows
        the error and the calling script stops - rather than only the log
        (quality-scale ``action-exceptions``). ``async_SetChargerProp`` has already
        logged the cause, so this only raises.

        Args:
            identifier: The charger property to write.
            value: The value to write.
            force: Write even if the charger does not report the property.
            force_type: The JSON type to coerce the value to.

        Raises:
            HomeAssistantError: If the charger did not take the value.
        """
        if not await async_SetChargerProp(self._charger, identifier, value, force=force, force_type=force_type):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="entity_write_failed",
                translation_placeholders={"entity": str(self.entity_id), "property": identifier},
            )

    def _action_failure(self, action: str, e: Exception) -> HomeAssistantError:
        """Log an unexpected entity-action failure and return the error to raise for it.

        The entity-action counterpart of ``services.py::_raise_service_failure``:
        called from a handler rather than being one, so the traceback is attached
        from the exception it was handed.

        Args:
            action: The action method's name, used as the log context.
            e: The unexpected exception.

        Returns:
            The ``HomeAssistantError`` the caller should raise from ``e``.
        """
        _LOGGER.error(
            "%s - %s: %s failed: %s (%s.%s)",
            self._charger_id,
            self._identifier,
            action,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
            exc_info=e,
        )
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={"entity": str(self.entity_id), "action": action, "error": str(e)},
        )

    def _init_platform_specific(self) -> None:
        """Platform specific init actions."""
        # do nothing here as this is only a drop-in option for other platforms
        # do not put actions in a try / except block - execeptions should be covered by __init__
        pass

    async def async_added_to_hass(self) -> None:
        """Subscribe to pushed updates for this entity's property and seed its state.

        Property-source entities receive live updates via a dispatcher signal
        keyed on (config entry, property id); the subscription is released
        automatically on removal through ``async_on_remove``.

        The charger only pushes a property when it *changes*, and the first poll
        tick is up to 30 seconds away, so an entity would otherwise sit at
        'unknown' after every restart even though the value is already known.
        Seed it from the charger straight away.
        """
        await super().async_added_to_hass()
        if self._source == "property":
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    property_update_signal(self._entry.entry_id, self._identifier),
                    self._handle_property_update,
                )
            )
        self.hass.async_create_task(self.async_local_poll())

    @callback
    def _handle_property_update(self, value: Any) -> None:
        """Handle a dispatched property update by pushing the new state."""
        self.hass.async_create_task(self.async_local_push(value))

    def _check_firmware_supported(self) -> bool:
        """Return if the current charger firmware supports this entity."""
        fw_tst = self._entity_cfg.get("firmware", None)
        if fw_tst is None:
            return True
        fw = getattr(self._charger, "firmware", GetChargerProp(self._charger, "onv", None))
        if fw is None:
            _LOGGER.error(
                "%s - %s: _check_firmware_supported: Cannot identify Charger firmware: %s",
                self._charger_id,
                self._identifier,
                fw,
            )
            return False
        if fw_tst[:2] == ">=":
            v = Version(fw) >= Version(fw_tst[2:])
        elif fw_tst[:2] == "<=":
            v = Version(fw) <= Version(fw_tst[2:])
        elif fw_tst[:2] == "==":
            v = Version(fw) == Version(fw_tst[2:])
        elif fw_tst[:1] == ">":
            v = Version(fw) > Version(fw_tst[1:])
        elif fw_tst[:1] == "<":
            v = Version(fw) < Version(fw_tst[1:])
        else:
            _LOGGER.error(
                "%s - %s: _check_firmware_supported: Invalid firmware version test string: %s",
                self._charger_id,
                self._identifier,
                fw_tst,
            )
            return False
        _LOGGER.debug(
            "%s - %s: _check_firmware_supported complete (%s%s -> %s)",
            self._charger_id,
            self._identifier,
            fw,
            fw_tst,
            v,
        )
        return v

    def _check_variant_supported(self) -> bool:
        """Return if the current charger variant supports this entity."""
        v_tst = self._entity_cfg.get("variant", None)
        if v_tst is None:
            return True
        variant = GetChargerProp(self._charger, "var", 11)
        v = str(variant).upper() == str(v_tst).upper()
        _LOGGER.debug(
            "%s - %s: _check_variant_supported complete (%s=%s -> %s)",
            self._charger_id,
            self._identifier,
            variant,
            v_tst,
            v,
        )
        return v

    def _check_connection_supported(self) -> bool:
        """Return if the current charger connection type supports this entity."""
        c_tst = self._entity_cfg.get("connection", None)
        if c_tst is None:
            return True
        # An entry without a connection type predates the cloud option and was
        # connected locally, which is also how async_ConnectCharger reads it.
        connection = self._entry.data.get(CONF_CONNECTION, CONF_LOCAL)
        v = str(connection).upper() == str(c_tst).upper()
        _LOGGER.debug(
            "%s - %s: _check_connection_supported complete (%s=%s -> %s)",
            self._charger_id,
            self._identifier,
            connection,
            c_tst,
            v,
        )
        return v

    @property
    def description(self) -> str | None:
        """Return the description of the entity."""
        # The description is stored in the extra-state-attributes dict at init;
        # there is no separate self._description attribute to read.
        return self._attributes.get("description")

    @property
    def entity_category(self) -> EntityCategory | None:
        """Return the entity_category of the entity."""
        if self._entity_category is not None:
            return EntityCategory(self._entity_category)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the entity."""
        return self._attributes

    def _index_namespace(self, value: Any) -> Any:
        """Safely index a namespacelist value; None if not usable.

        The backing property can be missing or not a list on some firmware
        (e.g. 'cards' was removed in go-e firmware 60.0, so GetChargerProp
        returns the int default_state), so guard against non-subscriptable
        values and out-of-range indexes instead of indexing blindly.
        """
        if not isinstance(value, (list, tuple)):
            return None
        idx = int(self._namespace_id)
        if idx < 0 or idx >= len(value):
            return None
        return value[idx]

    def _reduce_list(self, values: list[Any]) -> Any:
        """Return the single state a catalog's ``value_reduce`` asks for, or None.

        Some list properties do not put their value at a fixed index: 'tma'
        reports the charger's temperature sensors at indexes 0..1 on older
        hardware and at 2..5 on current hardware (a Flex on firmware 43.4
        answers ``[null, null, 29.75, 33.625, 28.625, 27]``), so no 'value_id'
        addresses both. Reducing over the entries the charger actually reports
        does, and skipping the nulls is what keeps the entity off its default
        state. Returns None when nothing is reported yet.
        """
        reported = [value for value in values if value is not None]
        if not reported:
            return None
        reducer = str(self._entity_cfg.get("value_reduce")).lower()
        if reducer == "max":
            return max(reported)
        if reducer == "min":
            return min(reported)
        if reducer == "first":
            return reported[0]
        _LOGGER.error(
            "%s - %s: _reduce_list failed: unknown value_reduce: %s",
            self._charger_id,
            self._identifier,
            reducer,
        )
        return None

    def _update_list_attributes(self, values: list[Any]) -> None:
        """Expose the individual entries of a reduced list as extra attributes.

        Uses the same ``<name>:<index>`` ``attribute_ids`` syntax as a
        'value_id' list, but an index the charger does not report (out of
        range, or null on this hardware generation) is dropped rather than
        shown as an empty attribute.
        """
        for attr_entry in self._entity_cfg.get("attribute_ids") or []:
            attr_id, _, attr_index = str(attr_entry).partition(":")
            index = int(attr_index)
            value = values[index] if 0 <= index < len(values) else None
            if value is None:
                self._attributes.pop(attr_id, None)
            else:
                self._attributes[attr_id] = value

    def _property_reported(self) -> bool:
        """Return whether the charger reports this entity's property at all.

        A property the charger does not have at all means the entity cannot
        exist. A property it reports as null is a different case: the value is
        only missing for now - a paired inverter that is briefly unreachable,
        say - so the entity is created and recovers when the next value arrives
        instead of disappearing until the config entry is reloaded.
        """
        return self._identifier in getattr(self._charger, "all_properties", {})

    def _get_namespacelist_item(self) -> Any:
        """Return the configured namespace item from the charger, or None."""
        return self._index_namespace(GetChargerProp(self._charger, self._identifier, self._default_state))

    def _update_attribute_props(self) -> None:
        """Copy sibling charger properties into the entity's extra attributes.

        Some values that belong next to a state live in a property of their own
        rather than inside the state's value, so they cannot be reached with
        ``attribute_ids`` (which only indexes into a namespace or list value).
        A catalog's ``attribute_props`` maps the attribute name to expose onto
        the id of the property to read it from - e.g. an RFID card's name in
        'c0n' alongside its energy in 'c0e'.
        """
        for attr_name, prop_id in (self._entity_cfg.get("attribute_props") or {}).items():
            self._attributes[str(attr_name)] = GetChargerProp(self._charger, str(prop_id), STATE_UNKNOWN)

    def _resolve_value_prop(self, state: Any) -> Any:
        """Return the state read from the property a raw code points at.

        A catalog's ``value_props`` maps a raw charger code onto the id of the
        property holding the value to show for it: 'trx' reports which RFID slot
        authorised the running session (code 1 -> slot 0), while the name to
        display lives in that slot's own 'c0n' property. A code with no entry
        (no chip, no transaction) has no property to read, so the state is
        unknown. Note the entity still updates on its own id's pushes, so a
        value renamed on the charger shows up at the next code change or poll.
        """
        value_props = self._entity_cfg.get("value_props") or {}
        prop_id = value_props.get(state)
        if prop_id is None:
            _LOGGER.debug(
                "%s - %s: _resolve_value_prop: no property mapped for state: %s",
                self._charger_id,
                self._identifier,
                state,
            )
            return STATE_UNKNOWN
        return GetChargerProp(self._charger, str(prop_id), STATE_UNKNOWN)

    @property
    def available(self) -> bool:
        """Return if device is available."""
        if self._init_failed is True:
            _LOGGER.debug(
                "%s - %s: available: false because enitity init not complete", self._charger_id, self._identifier
            )
            return False
        elif self._fw_supported is False:
            _LOGGER.debug(
                "%s - %s: available: false because entity not supported by charger firmware version",
                self._charger_id,
                self._identifier,
            )
            return False
        elif self._variant_supported is False:
            _LOGGER.debug(
                "%s - %s: available: false because entity not supported by charger variant (11kW/22kW)",
                self._charger_id,
                self._identifier,
            )
            return False
        elif self._connection_supported is False:
            _LOGGER.debug(
                "%s - %s: available: false because entity not supported by charger connection type (local/cloud)",
                self._charger_id,
                self._identifier,
            )
            return False
        elif not getattr(self._charger, "connected", True):
            _LOGGER.debug("%s - %s: available: false because charger disconnected", self._charger_id, self._identifier)
            return False
        elif not getattr(self._charger, "properties_initialized", True):
            _LOGGER.debug(
                "%s - %s: available: false because not all properties initialized", self._charger_id, self._identifier
            )
            return False
        elif self._source == "attribute" and not hasattr(self._charger, self._identifier):
            _LOGGER.debug("%s - %s: available: false because unknown attribute", self._charger_id, self._identifier)
            return False
        elif (
            self._source == "property" and GetChargerProp(self._charger, self._identifier, self._default_state) is None
        ):
            _LOGGER.debug("%s - %s: available: false because unknown property", self._charger_id, self._identifier)
            return False
        elif self._source == "namespacelist" and self._get_namespacelist_item() is None:
            _LOGGER.debug(
                "%s - %s: available: false because unknown namespacelist item: %s",
                self._charger_id,
                self._identifier,
                self._namespace_id,
            )
            return False
        else:
            return True

    @property
    def should_poll(self) -> bool:
        """Return True if polling is needed.

        Attribute and namespacelist sources have no push channel, so they always
        poll. A property source polls only until its first push arrives: while it
        still holds the default state we poll to seed an initial value, then rely
        on the property callback for subsequent updates. A state of None counts as
        unseeded too: platforms whose device classes reject the STATE_UNKNOWN
        string (sensor enum/timestamp) start from None instead of that sentinel.
        """
        state = getattr(self, self._state_attr, STATE_UNKNOWN)
        return bool(
            self._source in {"attribute", "namespacelist"}
            or state is None
            or state == self._entity_cfg.get("default_state", STATE_UNKNOWN)
        )

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return False if the entity should be disable by default."""
        try:
            enabled = self._entity_cfg.get("enabled", True)
            return not (enabled is False or str(enabled).lower() == "false")
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: entity_registry_enabled_default failed - default enable: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return True

    @property
    def device_info(self) -> DeviceInfo:
        """Return a device description for device registry."""
        # _LOGGER.debug("%s - %s: device_info", self._charger_id, self._identifier)
        # A detail the charger does not report is left out (None) rather than shown
        # as the literal string "unknown" on the device page.
        variant = GetChargerProp(self._charger, "var", None)
        info = DeviceInfo(
            identifiers={(DOMAIN, getattr(self._charger, "serial", GetChargerProp(self._charger, "sse", None)))},
            manufacturer=getattr(self._charger, "manufacturer", None) or None,
            model=GetChargerProp(self._charger, "typ", getattr(self._charger, "device_type", None)) or None,
            name=getattr(self._charger, "name", getattr(self._charger, "hostname", None)) or DEFAULT_NAME,
            sw_version=getattr(self._charger, "firmware", None) or None,
            hw_version=f"{variant} kW" if variant is not None else None,
        )
        # _LOGGER.debug("%s - %s: device_info result: %s", self._charger_id, self._identifier, info)
        return info

    async def async_update(self) -> None:
        """Async: Get latest data and states for the entity."""
        try:
            if not self.enabled:
                return None
            if not self.available:
                return None
            # _LOGGER.debug("%s - %s: async_update", self._charger_id, self._identifier)
            if self.should_poll:
                _LOGGER.debug("%s - %s: async_update is done via poll - initiate", self._charger_id, self._identifier)
                await self.async_local_poll()
            else:
                _LOGGER.debug(
                    "%s - %s: async_update is done via push - do nothing / wait for push event",
                    self._charger_id,
                    self._identifier,
                )
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: async_update failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )

    async def _async_update_validate_property(self, state: Any = None) -> Any:
        """Async: Validate the given state object, set attributes if necessary and return new single state."""
        try:
            # _LOGGER.debug("%s - %s: _async_update_validate_property", self._charger_id, self._identifier)
            self._update_attribute_props()
            if self._entity_cfg.get("value_props") is not None:
                return self._resolve_value_prop(state)
            if str(state).startswith("namespace"):
                _LOGGER.debug(
                    "%s - %s: _async_update_validate_property: process namespace value",
                    self._charger_id,
                    self._identifier,
                )
                namespace = state
                if self._entity_cfg.get("value_id", None) is None:
                    _LOGGER.error(
                        "%s - %s: _async_update_validate_property failed: please specify the 'value_id' to use",
                        self._charger_id,
                        self._identifier,
                    )
                    return None
                state = getattr(namespace, self._entity_cfg.get("value_id", STATE_UNKNOWN), STATE_UNKNOWN)
                for attr_id in self._entity_cfg.get("attribute_ids") or []:
                    self._attributes[attr_id] = getattr(namespace, attr_id, STATE_UNKNOWN)
            elif isinstance(state, list):
                state_list = state
                if self._entity_cfg.get("value_reduce") is not None:
                    state = self._reduce_list(state_list)
                    self._update_list_attributes(state_list)
                elif self._entity_cfg.get("value_id", None) is None:
                    state = state_list[0]
                    i = 1
                    for attr_state in state_list[1:]:
                        self._attributes["state" + str(i)] = attr_state
                        i = i + 1
                else:
                    state = state_list[int(self._entity_cfg.get("value_id", 0))]
                    for attr_entry in self._entity_cfg.get("attribute_ids") or []:
                        attr_id = attr_entry.split(":")[0]
                        attr_index = attr_entry.split(":")[1]
                        self._attributes[attr_id] = state_list[int(attr_index)]
            return state
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: _async_update_validate_property failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return None

    async def _async_update_validate_platform_state(self, state: Any = None) -> Any:
        """Async: Validate the given state for platform specific requirements."""
        # do nothing here as this is only a drop-in option for other platforms
        # return None if validation failed
        return state

    async def async_local_poll(self) -> None:
        """Async: Poll the latest data and states from the entity."""
        try:
            _LOGGER.debug("%s - %s: async_local_poll", self._charger_id, self._identifier)
            if self._source == "attribute":
                state = getattr(self._charger, self._identifier, self._default_state)
            elif self._source == "namespacelist":
                state = self._get_namespacelist_item()
                _LOGGER.debug(
                    "%s - %s: async_local_poll namespace pre validate state of %s: %s",
                    self._charger_id,
                    self._identifier,
                    self._attr_unique_id,
                    state,
                )
                if state is not None:
                    state = await self._async_update_validate_property(state)
                _LOGGER.debug(
                    "%s - %s: async_local_poll namespace post validate state of %s: %s",
                    self._charger_id,
                    self._identifier,
                    self._attr_unique_id,
                    state,
                )
            elif self._source == "property":
                state = await async_GetChargerProp(self._charger, self._identifier, self._default_state)
                state = await self._async_update_validate_property(state)

            state = await self._async_update_validate_platform_state(state)
            if state is not None:
                setattr(self, self._state_attr, state)
                self.async_write_ha_state()
            # _LOGGER.debug("%s - %s: async_local_poll complete: %s", self._charger_id, self._identifier, state)
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: async_local_poll failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )

    async def async_local_push(self, state: Any = None) -> None:
        """Async: Get the latest status from the entity after an update was pushed."""
        try:
            if not self.enabled:
                return None
            _LOGGER.debug("%s - %s: async_local_push", self._charger_id, self._identifier)
            if self._source == "attribute":
                pass
            elif self._source == "namespacelist":
                state = self._index_namespace(state)
                if state is not None:
                    state = await self._async_update_validate_property(state)
            elif self._source == "property":
                state = await self._async_update_validate_property(state)

            state = await self._async_update_validate_platform_state(state)
            if state is not None:
                setattr(self, self._state_attr, state)
                self.async_write_ha_state()
                # _LOGGER.debug("%s - %s: async_local_push complete: %s", self._charger_id, self._identifier, state)
            else:
                await self.async_local_poll()
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: async_local_push failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
