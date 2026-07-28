"""Select entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.select import SelectEntity
from homeassistant.util import slugify

from .catalog import async_setup_catalog_entities
from .entities import ChargerPlatformEntity
from .utils import async_SetChargerProp

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER: Final = logging.getLogger(__name__)
platform = "select"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
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
        """
        self._opt_identifier = self._entity_cfg.get("options", None)
        if isinstance(self._opt_identifier, dict):
            self._opt_dict = self._opt_identifier
            self._opt_out = {k: slugify(str(v)) for k, v in self._opt_dict.items()}
        else:
            opts = getattr(self._charger, str(self._opt_identifier), None)
            self._opt_dict = opts if isinstance(opts, dict) else {}
            self._opt_out = dict(self._opt_dict)
        self._attr_options = list(self._opt_out.values())
        # _LOGGER.debug("%s - %s: __init__ attr_options: %s)", self._charger_id, self._identifier, self._attr_options)

    async def _async_update_validate_platform_state(self, state: Any = None) -> Any:
        """Async: Validate the given state for select specific requirements."""
        try:
            if state in self._opt_dict:
                state = self._opt_out[state]
            elif state in self._opt_out.values():
                pass
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
            _LOGGER.error(
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
                _LOGGER.error(
                    "%s - %s: async_select_option: option %s not within options: %s",
                    self._charger_id,
                    self._identifier,
                    option,
                    self._opt_out,
                )
                return None
            _LOGGER.debug("%s - %s: async_select_option: save option key %s", self._charger_id, self._identifier, key)
            await async_SetChargerProp(self._charger, self._identifier, key, force_type=self._set_type)
        except Exception as e:
            _LOGGER.error(
                "%s - %s: async_select_option failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
