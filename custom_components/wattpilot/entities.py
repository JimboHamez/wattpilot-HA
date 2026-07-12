"""Base entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from packaging.version import Version

from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PARAMS, STATE_UNKNOWN, EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import CONF_CONNECTION, DEFAULT_NAME, DOMAIN
from .utils import GetChargerProp, async_GetChargerProp, property_update_signal

if TYPE_CHECKING:
    from wattpilot_api import Wattpilot

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

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

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, entity_cfg: dict[str, Any], charger: Wattpilot) -> None:
        """Initialize the object."""
        try:
            self._charger_id = str(entry.data.get(CONF_FRIENDLY_NAME, entry.data.get(CONF_IP_ADDRESS, DEFAULT_NAME)))
            self._identifier = str(entity_cfg.get("id")).split("_")[0]
            _LOGGER.debug("%s - %s: __init__", self._charger_id, self._identifier)

            self._charger = charger
            self._source = entity_cfg.get("source", "property")
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
            if self._fw_supported is not False:
                if self._source == "attribute" and not hasattr(self._charger, self._identifier):
                    _LOGGER.error(
                        "%s - %s: __init__: Charger does not have an attribute: %s (maybe a property?)",
                        self._charger_id,
                        self._identifier,
                        self._identifier,
                    )
                    self._init_failed = True
                elif (
                    self._source == "property"
                    and GetChargerProp(self._charger, self._identifier, self._default_state) is None
                ):
                    _LOGGER.error(
                        "%s - %s: __init__: Charger does not have a property: %s (maybe an attribute?)",
                        self._charger_id,
                        self._identifier,
                        self._identifier,
                    )
                    self._init_failed = True
                elif self._source == "namespacelist" and self._get_namespacelist_item() is None:
                    _LOGGER.error(
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
            self._attr_icon = self._entity_cfg.get("icon", None)
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
            _LOGGER.error(
                "%s - %s: __init__ failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return None

    def _init_platform_specific(self) -> None:
        """Platform specific init actions."""
        # do nothing here as this is only a drop-in option for other platforms
        # do not put actions in a try / except block - execeptions should be covered by __init__
        pass

    async def async_added_to_hass(self) -> None:
        """Subscribe to pushed updates for this entity's property.

        Property-source entities receive live updates via a dispatcher signal
        keyed on (config entry, property id); the subscription is released
        automatically on removal through ``async_on_remove``.
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
        entry_data = getattr(self._entry, "runtime_data", None)
        if entry_data is None:
            return True
        config_params = entry_data.get(CONF_PARAMS, None)
        if config_params is None:
            return True
        connection = config_params.get(CONF_CONNECTION, STATE_UNKNOWN)
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

    def _get_namespacelist_item(self) -> Any:
        """Return the configured namespace item from the charger, or None."""
        return self._index_namespace(GetChargerProp(self._charger, self._identifier, self._default_state))

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
        on the property callback for subsequent updates.
        """
        return bool(
            self._source in {"attribute", "namespacelist"}
            or getattr(self, self._state_attr, STATE_UNKNOWN) == self._entity_cfg.get("default_state", STATE_UNKNOWN)
        )

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return False if the entity should be disable by default."""
        try:
            enabled = self._entity_cfg.get("enabled", True)
            return not (enabled is False or str(enabled).lower() == "false")
        except Exception as e:
            _LOGGER.error(
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
        info = DeviceInfo(
            identifiers={(DOMAIN, getattr(self._charger, "serial", GetChargerProp(self._charger, "sse", None)))},
            manufacturer=getattr(self._charger, "manufacturer", STATE_UNKNOWN),
            model=GetChargerProp(self._charger, "typ", getattr(self._charger, "device_type", STATE_UNKNOWN)),
            name=getattr(self._charger, "name", getattr(self._charger, "hostname", STATE_UNKNOWN)),
            sw_version=getattr(self._charger, "firmware", STATE_UNKNOWN),
            hw_version=str(GetChargerProp(self._charger, "var", STATE_UNKNOWN)) + " KW",
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
                await self.hass.async_create_task(self.async_local_poll())
            else:
                _LOGGER.debug(
                    "%s - %s: async_update is done via push - do nothing / wait for push event",
                    self._charger_id,
                    self._identifier,
                )
        except Exception as e:
            _LOGGER.error(
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
                if self._entity_cfg.get("value_id", None) is None:
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
            _LOGGER.error(
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
            _LOGGER.error(
                "%s - %s: async_local_poll failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )

    async def async_local_push(self, state: Any = None, initwait: bool = False) -> None:
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
                await self.hass.async_create_task(self.async_local_poll())
        except Exception as e:
            if type(e).__name__ == "NoEntitySpecifiedError" and initwait is False:
                _LOGGER.debug(
                    "%s - %s: async_local_push: wait and retry once for setup init delay",
                    self._charger_id,
                    self._identifier,
                )
                await asyncio.sleep(5)
                await self.async_local_push(state, True)
            else:
                _LOGGER.error(
                    "%s - %s: async_local_push failed: %s (%s.%s)",
                    self._charger_id,
                    self._identifier,
                    str(e),
                    e.__class__.__module__,
                    type(e).__name__,
                )
