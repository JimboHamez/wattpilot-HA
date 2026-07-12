"""Button entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Final

import aiofiles
import yaml

from homeassistant.components.button import ButtonEntity

from .const import CONF_CHARGER
from .entities import ChargerPlatformEntity
from .utils import async_SetChargerProp

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER: Final = logging.getLogger(__name__)
platform = "button"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the button platform."""
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
            entity_cfg["source"] = "none"
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
            entity = ChargerButton(hass, entry, entity_cfg, charger)
            if entity is None:
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


class ChargerButton(ChargerPlatformEntity, ButtonEntity):
    """Button class for Fronius Wattpilot integration."""

    def _init_platform_specific(self) -> None:
        """Platform specific init actions."""
        self._set_value = self._entity_cfg.get("set_value", None)
        if self._set_value is None:
            _LOGGER.error(
                "%s - %s: __init__: Required configuration option 'set_value' missing - please specify: %s",
                self._charger_id,
                self._identifier,
                self._set_value,
            )
            return None

    async def async_local_poll(self) -> None:
        """Async: Poll the latest data and states from the entity."""
        # no state required for ButtonEntity
        pass

    async def async_press(self) -> None:
        """Async: Handle button press."""
        try:
            await async_SetChargerProp(
                self._charger, self._identifier, self._set_value, force=True, force_type=self._set_type
            )
        except Exception as e:
            _LOGGER.error(
                "%s - %s: update failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
