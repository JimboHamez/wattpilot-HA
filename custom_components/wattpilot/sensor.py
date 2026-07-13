"""Sensor entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import aiofiles
import yaml

from homeassistant.components.sensor import (  # type: ignore[attr-defined]
    UNIT_CONVERTERS,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import STATE_UNKNOWN
from homeassistant.util import dt as dt_util, slugify

from .const import CONF_CHARGER
from .entities import ChargerPlatformEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER: Final = logging.getLogger(__name__)
platform = "sensor"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform."""
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

    for entity_cfg in yaml_cfg[platform]:
        try:
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
            entity = ChargerSensor(hass, entry, entity_cfg, charger)
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


class ChargerSensor(ChargerPlatformEntity, SensorEntity):
    """Sensor class for Fronius Wattpilot integration."""

    _state_attr = "_attr_native_value"

    def _init_platform_specific(self) -> None:
        """Platform specific init actions."""
        if "default_state" not in self._entity_cfg:
            # Home Assistant validates the very first state it writes (at add
            # time, before any poll or push) against the device class: an enum
            # sensor rejects anything outside its options, and a timestamp
            # sensor requires a datetime. The STATE_UNKNOWN *string* fails both
            # and the entity is dropped, so start from None instead — which HA
            # renders as 'unknown' anyway.
            self._attr_native_value = None
        self._attr_native_unit_of_measurement = self._entity_cfg.get("unit_of_measurement", None)
        if (
            unit_converter := UNIT_CONVERTERS.get(self._attr_device_class)
        ) is not None and self._attr_native_unit_of_measurement in unit_converter.VALID_UNITS:
            suggested = self._entity_cfg.get("suggested_unit_of_measurement", self._attr_native_unit_of_measurement)
            if suggested in unit_converter.VALID_UNITS:
                self._attr_suggested_unit_of_measurement = suggested
        if self._entity_cfg.get("state_class", None) is not None:
            self._attr_state_class = SensorStateClass(str(self._entity_cfg.get("state_class")).lower())
        if self._entity_cfg.get("enum", None) is not None:
            self._state_enum = dict(self._entity_cfg.get("enum") or {})
            # Expose enum sensors as translated enum device-class entities: the
            # native value is a stable slug per raw code, translated for display
            # via entity.sensor.<key>.state.<slug> in strings.json.
            self._enum_slugs = {k: slugify(str(v)) for k, v in self._state_enum.items()}
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(self._enum_slugs.values())
        if self._entity_cfg.get("html_unescape", None) is not None:
            self._html_unescape = True

    def _parse_timestamp(self, value: Any) -> datetime | None:
        """Parse a charger datetime string into a timezone-aware datetime.

        A 'timestamp' device_class sensor must expose a ``datetime`` (with
        tzinfo), not a string. The charger reports values like
        ``"2026-07-12T01:41:26.437 +10:00"`` — note the space before the UTC
        offset, which HA's parser does not accept — so normalise then parse.
        Returns ``None`` when the value can't be parsed.
        """
        if isinstance(value, datetime):
            return value
        normalised = re.sub(r"\s+(?=[+-]\d{2}:?\d{2}$|Z$)", "", str(value))
        parsed = dt_util.parse_datetime(normalised)
        if parsed is None:
            _LOGGER.debug("%s - %s: could not parse timestamp value: %s", self._charger_id, self._identifier, value)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return parsed

    async def _async_update_validate_platform_state(self, state: Any = None) -> Any:
        """Async: Validate the given state for sensor specific requirements."""
        try:
            # Timestamp sensors need a tz-aware datetime; return None (shown as
            # 'unknown') rather than a string, which HA rejects for this class.
            if self._attr_device_class == SensorDeviceClass.TIMESTAMP:
                if state in (None, "None", STATE_UNKNOWN):
                    return None
                return self._parse_timestamp(state)
            if state is None or state == "None":
                # Numeric sensors (temperature, power, energy, …) and enum
                # sensors reject the 'unknown' string; HA only accepts None (or,
                # for enum, an option) for a missing value. Returning None leaves
                # the last value in place rather than raising ValueError. Only
                # plain text sensors may display the STATE_UNKNOWN string.
                if self._numeric_state_expected or self._attr_device_class == SensorDeviceClass.ENUM:
                    return None
                state = STATE_UNKNOWN
            elif hasattr(self, "_html_unescape") and self._html_unescape:
                state = html.unescape(state)
            elif not hasattr(self, "_state_enum"):
                pass
            elif state in self._state_enum:
                state = self._enum_slugs[state]
            elif state in self._enum_slugs.values():
                pass
            else:
                # Unknown enum code: return None rather than a value outside the
                # option list, which Home Assistant rejects for an enum sensor.
                _LOGGER.warning(
                    "%s - %s: _async_update_validate_platform_state failed: state %s not within enum values: %s",
                    self._charger_id,
                    self._identifier,
                    state,
                    self._state_enum,
                )
                return None
            if self._attr_native_unit_of_measurement is not None:
                self._attr_native_value = state
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
