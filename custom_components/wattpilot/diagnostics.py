"""Diagnostics support for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Final

import wattpilot_api

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD

from .const import CONF_SERIAL

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import WattpilotConfigEntry

# The entry's unique_id and title repeat what its data holds: a manually added
# local entry is keyed by its IP address, and without a friendly name the title
# is that address too. Redacting only the data would leave both in the download.
REDACT_CONFIG = {CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_SERIAL, "title", "unique_id"}
# Credentials (the cloud key, the WiFi access-point keys, OCPP secrets) and
# anything that identifies the charger, its network or its users: serial, names,
# hostname, SSIDs, MAC addresses, the OCPP server URL and the RFID card names.
REDACT_ALLPROPS = {
    "c0n",
    "c1n",
    "c2n",
    "c3n",
    "c4n",
    "c5n",
    "c6n",
    "c7n",
    "c8n",
    "c9n",
    "cak",
    "cards",
    "ccw",
    "data",
    "dll",
    "facwak",
    "ffna",
    "fna",
    "fwan",
    "host",
    "maca",
    "macs",
    "ocppcc",
    "ocppck",
    "ocppsc",
    "ocppu",
    "scan",
    "sse",
    "wak",
    "wan",
    "wcb",
    "wfb",
    "wifis",
    "wpb",
    "wss",
}

_LOGGER: Final = logging.getLogger(__name__)
platform = "diagnostics"


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: WattpilotConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    _LOGGER.debug("Returning %s platform entry: %s", platform, entry.entry_id)
    try:
        _LOGGER.debug(
            "%s - async_get_config_entry_diagnostics %s: Getting charger instance from data store",
            entry.entry_id,
            platform,
        )
        charger = entry.runtime_data.charger
    except Exception as e:
        _LOGGER.exception(
            "%s - async_get_config_entry_diagnostics %s: Getting charger instance from data store failed: %s (%s.%s)",
            entry.entry_id,
            platform,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return {}

    try:
        _LOGGER.debug(
            "%s - async_get_config_entry_diagnostics %s: Add config entry configuration to output",
            entry.entry_id,
            platform,
        )
        diag: dict[str, Any] = {"config": async_redact_data(entry.as_dict(), REDACT_CONFIG)}
    except Exception as e:
        _LOGGER.exception(
            "%s - async_get_config_entry_diagnostics %s: Adding config entry configuration failed: %s (%s.%s)",
            entry.entry_id,
            platform,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return {}

    try:
        _LOGGER.debug(
            "%s - async_get_config_entry_diagnostics %s: Add charger properties to output", entry.entry_id, platform
        )
        diag["charger_properties"] = async_redact_data(charger.all_properties, REDACT_ALLPROPS)
    except Exception as e:
        _LOGGER.exception(
            "%s - async_get_config_entry_diagnostics %s: Adding charger properties to output failed: %s (%s.%s)",
            entry.entry_id,
            platform,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return diag

    try:
        _LOGGER.debug(
            "%s - async_get_config_entry_diagnostics %s: Add python modules version", entry.entry_id, platform
        )
        diag["wattpilot_module"] = version("wattpilot-api")
        diag["wattpilot_file"] = wattpilot_api.__file__
        diag["pyyaml_module"] = version("pyyaml")
        diag["aiofiles_module"] = version("aiofiles")
        diag["packaging"] = version("packaging")
    except Exception as e:
        _LOGGER.exception(
            "%s - async_get_config_entry_diagnostics %s: Add python modules version failed: %s (%s.%s)",
            entry.entry_id,
            platform,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return diag

    return diag
