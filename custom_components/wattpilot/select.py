"""Select entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import slugify

from .catalog import async_setup_catalog_entities
from .const import DOMAIN
from .entities import ChargerPlatformEntity
from .utils import GetChargerProp, property_update_signal

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .models import WattpilotConfigEntry

_LOGGER: Final = logging.getLogger(__name__)
platform = "select"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed


async def async_setup_entry(
    hass: HomeAssistant, entry: WattpilotConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the select platform."""
    await async_setup_catalog_entities(hass, entry, async_add_entities, platform, ChargerSelect, source="property")


class ChargerSelect(ChargerPlatformEntity, SelectEntity):
    """Select class for Fronius Wattpilot integration."""

    _state_attr = "_attr_current_option"

    def _init_platform_specific(self) -> None:
        """Platform specific init actions.

        ``self._opt_dict`` always maps the raw charger key to its human label.
        ``self._opt_out`` maps the raw key to the value Home Assistant exposes
        as an option: a stable slug for static enums (translated for display via
        ``entity.select.<key>.state.<slug>`` in strings.json), or the label
        itself for dynamic options that come from a charger attribute at runtime
        and therefore cannot be translated at build time.

        A definition with ``options_property`` instead takes its options from a
        list property of the charger (``clp``, the app's charging-current
        presets): each raw value is its own label, and the list is rebuilt when
        the charger pushes a new one. A charger that does not report the
        property cannot offer the entity, so it is skipped like any other
        entity whose property is absent.
        """
        self._opt_identifier = self._entity_cfg.get("options", None)
        self._opt_property = self._entity_cfg.get("options_property", None)
        if self._opt_property is not None:
            if not isinstance(GetChargerProp(self._charger, str(self._opt_property), None), list):
                _LOGGER.debug(
                    "%s - %s: __init__: Charger does not have a list property for the options: %s",
                    self._charger_id,
                    self._identifier,
                    self._opt_property,
                )
                self._init_failed = True
                return None
            self._set_property_options(GetChargerProp(self._charger, str(self._opt_property), None))
            return None
        if isinstance(self._opt_identifier, dict):
            self._opt_dict = self._opt_identifier
            self._opt_out = {k: slugify(str(v)) for k, v in self._opt_dict.items()}
        else:
            opts = getattr(self._charger, str(self._opt_identifier), None)
            self._opt_dict = opts if isinstance(opts, dict) else {}
            self._opt_out = dict(self._opt_dict)
        self._attr_options = list(self._opt_out.values())
        # _LOGGER.debug("%s - %s: __init__ attr_options: %s)", self._charger_id, self._identifier, self._attr_options)

    def _set_property_options(self, values: Any) -> None:
        """Rebuild the option maps from the list a charger property reports.

        The raw values double as the labels, formatted so an integral number
        reads as an integer ('16', not '16.0'). Anything that is not a list, or
        an empty one, leaves the entity without options rather than failing.
        """
        presets = values if isinstance(values, list) else []
        self._opt_dict = {value: self._format_option(value) for value in presets if value is not None}
        self._opt_out = dict(self._opt_dict)
        self._attr_options = list(self._opt_out.values())

    @staticmethod
    def _format_option(value: Any) -> str:
        """Return the option label for a raw list value."""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    async def async_added_to_hass(self) -> None:
        """Subscribe to the property the options come from, on top of the state property."""
        await super().async_added_to_hass()
        if self._opt_property is not None:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    property_update_signal(self._entry.entry_id, str(self._opt_property)),
                    self._handle_options_update,
                )
            )

    @callback
    def _handle_options_update(self, value: Any) -> None:
        """Rebuild the options from a pushed list and re-check the current state against them.

        The poll re-reads the state property, so an option that disappeared
        from the list clears the selection and a state that just gained an
        option picks it up; either way the new option list reaches Home
        Assistant with the state write.
        """
        _LOGGER.debug("%s - %s: _handle_options_update: %s", self._charger_id, self._identifier, value)
        self._set_property_options(value)
        self.hass.async_create_task(self.async_local_poll())

    async def _async_update_validate_platform_state(self, state: Any = None) -> Any:
        """Async: Validate the given state for select specific requirements."""
        try:
            if state in self._opt_dict:
                state = self._opt_out[state]
            elif state in self._opt_out.values():
                pass
            elif self._opt_property is not None:
                # A value between two presets (13 A set from the number entity,
                # say) is a normal state for a property-backed list, not a
                # fault: show no selection rather than the last preset, which
                # would be wrong. The base update logic only writes a state it
                # was handed, so the clear has to be written from here.
                _LOGGER.debug(
                    "%s - %s: _async_update_validate_platform_state: state %s is not a %s option: %s",
                    self._charger_id,
                    self._identifier,
                    state,
                    self._opt_property,
                    self._attr_options,
                )
                self._attr_current_option = None
                if self.entity_id:
                    self.async_write_ha_state()
                return None
            else:
                # Unknown value: return None so the current option is left
                # unchanged rather than writing a value outside the option list,
                # which Home Assistant rejects for a select entity.
                _LOGGER.error(
                    "%s - %s: _async_update_validate_platform_state failed: state %s not within options: %s",
                    self._charger_id,
                    self._identifier,
                    state,
                    self._opt_out,
                )
                return None
            return state
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: _async_update_validate_platform_state failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return None

    async def async_select_option(self, option: str) -> None:
        """Async: Change the selected option."""
        try:
            # Map the exposed option (slug or label) back to the raw charger key.
            key = next((k for k, v in self._opt_out.items() if v == option), None)
            if key is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_option",
                    translation_placeholders={
                        "entity": str(self.entity_id),
                        "option": str(option),
                        "options": ", ".join(str(v) for v in self._opt_out.values()),
                    },
                )
            _LOGGER.debug("%s - %s: async_select_option: save option key %s", self._charger_id, self._identifier, key)
            await self._async_write_property(self._identifier, key, force_type=self._set_type)
        except HomeAssistantError:
            raise
        except Exception as e:
            raise self._action_failure("async_select_option", e) from e
