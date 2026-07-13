"""Number entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Final

import aiofiles
import yaml

from homeassistant.components.number import UNIT_CONVERTERS, NumberEntity, NumberMode  # type: ignore[attr-defined]

from .const import CONF_CHARGER
from .entities import ChargerPlatformEntity
from .utils import async_SetChargerProp

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER: Final = logging.getLogger(__name__)
platform = "number"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the number platform."""
    _LOGGER.debug("Setting up %s platform entry: %s", platform, entry.entry_id)
    entites = []
    try:
        _LOGGER.debug("%s - async_setup_entry %s: Reading static yaml configuration", entry.entry_id, platform)
        async with aiofiles.open(os.path.dirname(os.path.realpath(__file__)) + "/" + platform + ".yaml") as y:
            yaml_cfg = yaml.safe_load(await y.read())
    except Exception as e:
        _LOGGER.error(
            "%s - async_setup_entry %s: Reading static yaml configuration failed: %s (%s.%s)",
            entry.entry_id,
            platform,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return

    try:
        _LOGGER.debug("%s - async_setup_entry %s: Getting charger instance from data store", entry.entry_id, platform)
        charger = entry.runtime_data[CONF_CHARGER]
    except Exception as e:
        _LOGGER.error(
            "%s - async_setup_entry %s: Getting charger instance from data store failed: %s (%s.%s)",
            entry.entry_id,
            platform,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return

    for entity_cfg in yaml_cfg.get(platform, []):
        try:
            entity_cfg["source"] = "property"
            if "id" not in entity_cfg or entity_cfg["id"] is None:
                _LOGGER.error(
                    "%s - async_setup_entry %s: Invalid yaml configuration - no id: %s",
                    entry.entry_id,
                    platform,
                    entity_cfg,
                )
                continue
            elif "source" not in entity_cfg or entity_cfg["source"] is None:
                _LOGGER.error(
                    "%s - async_setup_entry %s: Invalid yaml configuration - no source: %s",
                    entry.entry_id,
                    platform,
                    entity_cfg,
                )
                continue
            entity = ChargerNumber(hass, entry, entity_cfg, charger)
            if getattr(entity, "_init_failed", True):
                continue
            entites.append(entity)
            await asyncio.sleep(0)
        except Exception as e:
            _LOGGER.error(
                "%s - async_setup_entry %s: Reading static yaml configuration failed: %s (%s.%s)",
                entry.entry_id,
                platform,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return

    _LOGGER.info("%s - async_setup_entry: setup %s %s entities", entry.entry_id, len(entites), platform)
    if not entites:
        return
    async_add_entities(entites)


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
                await async_SetChargerProp(self._charger, "esk", True)
            await async_SetChargerProp(self._charger, self._identifier, value * self._factor, force_type=self._set_type)
        except Exception as e:
            _LOGGER.error(
                "%s - %s: update failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
