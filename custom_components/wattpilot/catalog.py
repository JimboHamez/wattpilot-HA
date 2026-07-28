"""Shared YAML-catalog setup for the Fronius Wattpilot platforms.

Every platform is data-driven: its entities come from a ``<platform>.yaml``
catalog sitting next to its Python module (see the header comment in
``sensor.yaml`` for the supported fields). The steps are identical for all six
platforms -- read the catalog, pull the connected charger out of the config
entry's runtime data, then build one entity per definition -- so they live here
rather than being copied into each ``async_setup_entry``.

Failures follow the integration's log-and-degrade convention: they are logged
and the platform is skipped, never raised.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Final

import aiofiles
import yaml

from .const import CONF_CHARGER

if TYPE_CHECKING:
    from wattpilot_api import Wattpilot

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .entities import ChargerPlatformEntity

_LOGGER: Final = logging.getLogger(__name__)

CATALOG_DIR: Final = os.path.dirname(os.path.realpath(__file__))


async def async_load_catalog(entry: ConfigEntry, platform: str) -> list[Any] | None:
    """Read a platform's YAML catalog and return its entity definitions.

    Args:
        entry: The config entry being set up; used to tag the log lines.
        platform: The platform name, which is also the catalog's file stem.

    Returns:
        The list of entity definitions, or ``None`` if the catalog could not be
        read. A catalog that exists but holds no definitions is not an error and
        yields an empty list.
    """
    _LOGGER.debug("%s - async_setup_entry %s: Reading static yaml configuration", entry.entry_id, platform)
    try:
        async with aiofiles.open(os.path.join(CATALOG_DIR, f"{platform}.yaml")) as handle:
            yaml_cfg = yaml.safe_load(await handle.read())
    except (OSError, yaml.YAMLError) as e:
        _LOGGER.error(
            "%s - async_setup_entry %s: Reading static yaml configuration failed: %s (%s.%s)",
            entry.entry_id,
            platform,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return None
    if not isinstance(yaml_cfg, dict):
        return []
    return list(yaml_cfg.get(platform) or [])


def get_charger(entry: ConfigEntry, platform: str) -> Wattpilot | None:
    """Return the connected charger stored in the config entry's runtime data.

    Args:
        entry: The config entry being set up.
        platform: The platform name, used only for the log lines.

    Returns:
        The charger object, or ``None`` if the runtime data store does not hold
        one (the entry is not set up, or setup failed before connecting).
    """
    _LOGGER.debug("%s - async_setup_entry %s: Getting charger instance from data store", entry.entry_id, platform)
    try:
        charger: Wattpilot = entry.runtime_data[CONF_CHARGER]
    except (AttributeError, KeyError, TypeError) as e:
        _LOGGER.error(
            "%s - async_setup_entry %s: Getting charger instance from data store failed: %s (%s.%s)",
            entry.entry_id,
            platform,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return None
    return charger


def _definition_is_complete(entry: ConfigEntry, platform: str, entity_cfg: Any, required: tuple[str, ...]) -> bool:
    """Report whether a catalog definition carries every key its platform needs.

    Args:
        entry: The config entry being set up; used to tag the log lines.
        platform: The platform name, used only for the log lines.
        entity_cfg: A single entity definition from the catalog.
        required: The keys that must be present and non-``None``.

    Returns:
        ``True`` if the definition is usable, ``False`` if it was reported as
        incomplete and should be skipped.
    """
    for key in required:
        if entity_cfg.get(key) is None:
            _LOGGER.error(
                "%s - async_setup_entry %s: Invalid yaml configuration - no %s: %s",
                entry.entry_id,
                platform,
                key,
                entity_cfg,
            )
            return False
    return True


async def async_setup_catalog_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    entity_class: type[ChargerPlatformEntity],
    *,
    source: str | None = None,
    required: tuple[str, ...] = ("id", "source"),
) -> None:
    """Build and register every entity a platform's YAML catalog defines.

    Definitions that are incomplete, or whose entity fails its firmware, variant
    or connection gate during ``__init__``, are skipped individually; the rest of
    the catalog is still set up.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.
        async_add_entities: The callback that registers the finished entities.
        platform: The platform name, which is also the catalog's file stem.
        entity_class: The entity class to instantiate per definition.
        source: A value source to force onto every definition, overriding the
            catalog. ``None`` leaves each definition's own ``source`` in place,
            which is what the sensor platform needs.
        required: The definition keys that must be present and non-``None``.
    """
    _LOGGER.debug("Setting up %s platform entry: %s", platform, entry.entry_id)
    definitions = await async_load_catalog(entry, platform)
    if definitions is None:
        return
    charger = get_charger(entry, platform)
    if charger is None:
        return

    entities: list[ChargerPlatformEntity] = []
    for entity_cfg in definitions:
        try:
            if source is not None:
                entity_cfg["source"] = source
            if not _definition_is_complete(entry, platform, entity_cfg, required):
                continue
            entity = entity_class(hass, entry, entity_cfg, charger)
            # A gate that fails during __init__ sets _init_failed; a missing
            # attribute means __init__ did not get far enough to clear it.
            if getattr(entity, "_init_failed", True):
                continue
            entities.append(entity)
        except Exception as e:
            _LOGGER.exception(
                "%s - async_setup_entry %s: Building entity failed: %s (%s.%s)",
                entry.entry_id,
                platform,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return

    _LOGGER.info("%s - async_setup_entry: setup %s %s entities", entry.entry_id, len(entities), platform)
    if not entities:
        return
    async_add_entities(entities)
