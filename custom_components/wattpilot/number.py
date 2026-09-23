"""Number entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.number import UNIT_CONVERTERS, NumberEntity, NumberMode  # type: ignore[attr-defined]
from homeassistant.exceptions import HomeAssistantError

from .catalog import async_setup_catalog_entities
from .entities import ChargerPlatformEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .models import WattpilotConfigEntry

_LOGGER: Final = logging.getLogger(__name__)
platform = "number"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed


async def async_setup_entry(
    hass: HomeAssistant, entry: WattpilotConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the number platform."""
    await async_setup_catalog_entities(hass, entry, async_add_entities, platform, ChargerNumber, source="property")


class ChargerNumber(ChargerPlatformEntity, NumberEntity):
    """Number class for Fronius Wattpilot integration."""

    _state_attr = "_attr_native_value"
    _factor: float = 1.0

    def _init_platform_specific(self) -> None:
        """Platform specific init actions."""
        self._attr_native_unit_of_measurement = self._entity_cfg.get("unit_of_measurement", None)
        self._factor = float(self._entity_cfg.get("factor", 1) or 1)
        if (
            self._attr_device_class is not None
            and (unit_converter := UNIT_CONVERTERS.get(self._attr_device_class)) is not None
            and self._attr_native_unit_of_measurement in unit_converter.VALID_UNITS
        ):
            self._attr_suggested_unit_of_measurement = self._entity_cfg.get("unit_of_measurement", None)

        n = self._entity_cfg.get("native_min_value", None)
        if n is not None:
            self._attr_native_min_value = float(n)
        n = self._entity_cfg.get("native_max_value", None)
        if n is not None:
            self._attr_native_max_value = float(n)
        n = self._entity_cfg.get("native_step", None)
        if n is not None:
            self._attr_native_step = float(n)
        mode = self._entity_cfg.get("mode")
        if mode is not None:
            self._attr_mode = NumberMode(mode)

    def _get_platform_specific_state(self) -> Any:
        """Platform specific init actions."""
        return self.state

    async def _async_update_validate_platform_state(self, state: Any = None) -> Any:
        """Async: Validate the given state for sensor specific requirements."""
        if self._factor != 1 and isinstance(state, (int, float)) and not isinstance(state, bool):
            state = state / self._factor
        if self._attr_native_unit_of_measurement is not None:
            self._attr_native_value = state
        return state

    async def async_set_native_value(self, value: float) -> None:
        """Async: Change the current value."""
        try:
            _LOGGER.debug(
                "%s - %s: async_set_native_value: value was changed to: %s", self._charger_id, self._identifier, value
            )
            if self._identifier == "fte":
                # The next-trip energy target ('fte') is only honoured when the
                # charger is in kWh mode; force 'esk' on so the value is never
                # interpreted as kilometres.
                _LOGGER.debug(
                    "%s - %s: async_set_native_value: workaround: always set next trip distance to kWh not km",
                    self._charger_id,
                    self._identifier,
                )
                await self._async_write_property("esk", True)
            await self._async_write_property(self._identifier, value * self._factor, force_type=self._set_type)
        except HomeAssistantError:
            raise
        except Exception as e:
            raise self._action_failure("async_set_native_value", e) from e
