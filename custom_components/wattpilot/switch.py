"""Switch entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN

from .catalog import async_setup_catalog_entities
from .entities import ChargerPlatformEntity
from .utils import async_SetChargerProp

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER: Final = logging.getLogger(__name__)
platform = "switch"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed

# The charger encodes boolean properties inconsistently: some come back as JSON
# booleans, others as 0/1 numbers. Both have to resolve to a switch state, or the
# entity stays stuck at STATE_UNKNOWN.
TRUE_VALUES: Final = frozenset({"true", "1", "1.0", STATE_ON})
FALSE_VALUES: Final = frozenset({"false", "0", "0.0", STATE_OFF})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the switch platform."""
    await async_setup_catalog_entities(hass, entry, async_add_entities, platform, ChargerSwitch, source="property")


class ChargerSwitch(ChargerPlatformEntity):
    """Switch class for Fronius Wattpilot integration."""

    async def _async_update_validate_platform_state(self, state: Any = None) -> Any:
        """Async: Validate the given state for switch specific requirements."""
        try:
            if str(state) == STATE_UNKNOWN:
                pass
            elif str(state).lower() in TRUE_VALUES:
                state = STATE_ON
            elif str(state).lower() in FALSE_VALUES:
                state = STATE_OFF
            else:
                _LOGGER.warning(
                    "%s - %s: _async_update_validate_platform_state failed: state %s not valid for switch platform",
                    self._charger_id,
                    self._identifier,
                    state,
                )
                state = STATE_UNKNOWN

            if state == STATE_ON and self._entity_cfg.get("invert", False):
                _LOGGER.debug(
                    "%s - %s: _async_update_validate_platform_state: invert state: %s -> %s",
                    self._charger_id,
                    self._identifier,
                    STATE_ON,
                    STATE_OFF,
                )
                state = STATE_OFF
            elif state == STATE_OFF and self._entity_cfg.get("invert", False):
                _LOGGER.debug(
                    "%s - %s: _async_update_validate_platform_state: invert state: %s -> %s",
                    self._charger_id,
                    self._identifier,
                    STATE_OFF,
                    STATE_ON,
                )
                state = STATE_ON
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

    @property
    def is_on(self) -> bool:
        """Return true if entity is on."""
        return self.state == STATE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Async: Turn entity on."""
        try:
            _LOGGER.debug("%s - %s: async_turn_on: %s", self._charger_id, self._identifier, self._attr_translation_key)
            value = not self._entity_cfg.get("invert", False)
            await async_SetChargerProp(self._charger, self._identifier, value)
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: async_turn_on failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Async: Turn entity off."""
        try:
            _LOGGER.debug("%s - %s: async_turn_off: %s", self._charger_id, self._identifier, self._attr_translation_key)
            value = bool(self._entity_cfg.get("invert", False))
            await async_SetChargerProp(self._charger, self._identifier, value)
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: async_turn_off failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
