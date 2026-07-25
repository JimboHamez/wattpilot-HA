"""Init for the Fronius Wattpilot integration."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final, Literal

from wattpilot_api.exceptions import AuthenticationError

from homeassistant.const import CONF_PARAMS
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.loader import async_get_integration

from .availability import ChargerConnectionMonitor
from .const import (
    AUTH_FAILURE_REAUTH_THRESHOLD,
    CONF_CHARGER,
    CONF_DBG_PROPS,
    DOMAIN,
    FUNC_CONNECTION_MONITOR,
    FUNC_PROPERTY_UPDATES_CALLBACK,
    SUPPORTED_PLATFORMS,
)
from .services import (
    async_registerService,
    async_service_DisconnectCharger,
    async_service_ReConnectCharger,
    async_service_SetDebugProperties,
    async_service_SetGoECloud,
    async_service_SetNextTrip,
)
from .utils import async_ConnectCharger, async_DisconnectCharger, async_PropertyUpdateHandler

if TYPE_CHECKING:
    from wattpilot_api import Wattpilot

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER: Final = logging.getLogger(__name__)

# Maps entry_id -> consecutive password rejections seen during setup retries.
# The stored password is not the problem when this grows: a charger that has
# just been re-powered can reject its (correct) password for a few seconds while
# it boots, so those early failures are retried instead of forcing a reauth.
_AUTH_FAILURE_COUNTS: Final[dict[str, int]] = {}


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration-wide service actions.

    Services are registered here (not per config entry) so they exist even when
    no entry is loaded; each resolves its target charger from the device id in
    the service call.
    """
    try:
        _LOGGER.debug("%s - async_setup: register services", DOMAIN)
        await async_registerService(hass, "disconnect_charger", async_service_DisconnectCharger)
        await async_registerService(hass, "reconnect_charger", async_service_ReConnectCharger)
        await async_registerService(hass, "set_goe_cloud", async_service_SetGoECloud)
        await async_registerService(hass, "set_debug_properties", async_service_SetDebugProperties)
        await async_registerService(hass, "set_next_trip", async_service_SetNextTrip)
    except Exception as e:
        _LOGGER.error(
            "%s - async_setup: register services failed: %s (%s.%s)",
            DOMAIN,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a charger from the config entry."""
    _LOGGER.debug("Setting up config entry: %s", entry.entry_id)

    try:
        integration = await async_get_integration(hass, DOMAIN)
        v = integration.version
        if v:
            _LOGGER.debug("%s - async_setup_entry: %s integration version: %s", entry.entry_id, DOMAIN, v)
        else:
            _LOGGER.debug("%s - async_setup_entry: Unknown %s integration version", entry.entry_id, DOMAIN)
    except Exception:
        _LOGGER.warning("%s - async_setup_entry: Unable to determine %s integration version", entry.entry_id, DOMAIN)
        pass

    charger: Wattpilot | Literal[False] = False
    try:
        _LOGGER.debug("%s - async_setup_entry: Connecting charger", entry.entry_id)
        charger = await async_ConnectCharger(entry.entry_id, entry.data)
        # Signal "not ready" so Home Assistant retries setup later instead of
        # marking the entry permanently failed (e.g. charger briefly offline).
        if charger is False:
            raise ConfigEntryNotReady(f"Unable to connect to Wattpilot charger for entry {entry.entry_id}")
        # A clean connect clears any earlier transient password rejections.
        _AUTH_FAILURE_COUNTS.pop(entry.entry_id, None)
    except ConfigEntryNotReady:
        raise
    except AuthenticationError as e:
        # A charger that has just been re-powered can reject its (correct)
        # password for a few seconds while it boots. Retry those early failures
        # as "not ready"; only escalate to a reauth flow once the rejection has
        # persisted across AUTH_FAILURE_REAUTH_THRESHOLD consecutive attempts.
        failures = _AUTH_FAILURE_COUNTS.get(entry.entry_id, 0) + 1
        _AUTH_FAILURE_COUNTS[entry.entry_id] = failures
        if failures < AUTH_FAILURE_REAUTH_THRESHOLD:
            _LOGGER.warning(
                "%s - async_setup_entry: charger rejected the password (attempt %s of %s), "
                "retrying in case it is still starting up",
                entry.entry_id,
                failures,
                AUTH_FAILURE_REAUTH_THRESHOLD,
            )
            raise ConfigEntryNotReady(f"Charger rejected the password for entry {entry.entry_id}, retrying") from e
        # The rejection persisted: treat the stored password as genuinely wrong
        # and ask the user to re-enter it via a reauth flow.
        _AUTH_FAILURE_COUNTS.pop(entry.entry_id, None)
        raise ConfigEntryAuthFailed(f"Authentication failed for Wattpilot charger for entry {entry.entry_id}") from e
    except Exception as e:
        _LOGGER.error(
            "%s - async_setup_entry: Connecting charger failed: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await async_DisconnectCharger(entry.entry_id, charger)
        raise ConfigEntryNotReady(f"Error connecting to Wattpilot charger for entry {entry.entry_id}: {e}") from e

    try:
        _LOGGER.debug("%s - async_setup_entry: Creating runtime data store for %s", entry.entry_id, DOMAIN)
        entry.runtime_data = {
            CONF_CHARGER: charger,
            CONF_PARAMS: entry.data,
            CONF_DBG_PROPS: False,
        }
        entry_data = entry.runtime_data
    except Exception as e:
        _LOGGER.error(
            "%s - async_setup_entry: Creating data store failed: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await async_DisconnectCharger(entry.entry_id, charger)
        await async_unload_entry(hass, entry)
        return False

    try:
        _LOGGER.debug("%s - async_setup_entry: Trigger setup for platforms", entry.entry_id)
        await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)
    except Exception as e:
        _LOGGER.error(
            "%s - async_setup_entry: Setup trigger failed: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await async_unload_entry(hass, entry)
        return False

    try:
        _LOGGER.debug("%s - async_setup_entry: register properties update handler", entry.entry_id)

        # The wattpilot_api client fires property callbacks on Home Assistant's
        # own event loop, so an async callback can be registered directly.
        # on_property_change returns an unsubscribe function used on unload.
        async def _property_update_callback(identifier: str, value: Any) -> None:
            await async_PropertyUpdateHandler(hass, entry, identifier, value)

        entry_data[FUNC_PROPERTY_UPDATES_CALLBACK] = charger.on_property_change(_property_update_callback)
    except Exception as e:
        _LOGGER.error(
            "%s - async_setup_entry: Could not register properties updater handler: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await async_unload_entry(hass, entry)
        return False

    try:
        _LOGGER.debug("%s - async_setup_entry: start charger connection monitor", entry.entry_id)
        entry_data[FUNC_CONNECTION_MONITOR] = ChargerConnectionMonitor(hass, entry.entry_id, charger).async_start()
    except Exception as e:
        _LOGGER.error(
            "%s - async_setup_entry: Could not start charger connection monitor: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await async_unload_entry(hass, entry)
        return False

    _LOGGER.debug("%s - async_setup_entry: Completed", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        _LOGGER.debug("Unloading config entry: %s", entry.entry_id)
        _AUTH_FAILURE_COUNTS.pop(entry.entry_id, None)
        all_ok = True
        for platform in SUPPORTED_PLATFORMS:
            _LOGGER.debug("%s - async_unload_entry: unload platform: %s", entry.entry_id, platform)
            platform_ok = await asyncio.gather(*[hass.config_entries.async_forward_entry_unload(entry, platform)])

            if not platform_ok:
                _LOGGER.error(
                    "%s - async_unload_entry: failed to unload: %s (%s)", entry.entry_id, platform, platform_ok
                )
                all_ok = False

        if all_ok:
            entry_data = entry.runtime_data
            charger = entry_data[CONF_CHARGER]

            try:
                _LOGGER.debug("%s - async_unload_entry: stop charger connection monitor", entry.entry_id)
                # async_start returned the timer-cancel callable at setup.
                stop_monitor = entry_data.get(FUNC_CONNECTION_MONITOR)
                if callable(stop_monitor):
                    stop_monitor()
            except Exception as e:
                _LOGGER.error(
                    "%s - async_unload_entry: failed to stop charger connection monitor: %s (%s.%s)",
                    entry.entry_id,
                    str(e),
                    e.__class__.__module__,
                    type(e).__name__,
                )
                pass

            try:
                _LOGGER.debug("%s - async_unload_entry: remove registered event handlers", entry.entry_id)
                # on_property_change returned an unsubscribe callable at setup.
                unsubscribe = entry_data.get(FUNC_PROPERTY_UPDATES_CALLBACK)
                if callable(unsubscribe):
                    unsubscribe()
            except Exception as e:
                _LOGGER.error(
                    "%s - async_unload_entry: failed to remove registered event handlers: %s (%s.%s)",
                    entry.entry_id,
                    str(e),
                    e.__class__.__module__,
                    type(e).__name__,
                )
                pass

            try:
                await async_DisconnectCharger(entry.entry_id, charger)
                charger = None
            except Exception as e:
                _LOGGER.error(
                    "%s - async_unload_entry: could not disconnect charger: %s (%s.%s)",
                    entry.entry_id,
                    str(e),
                    e.__class__.__module__,
                    type(e).__name__,
                )
                _LOGGER.error(
                    "%s - async_unload_entry: session at charger %s (%s) stays open -> restart charger",
                    entry.entry_id,
                    charger.name,
                    charger.serial,
                )
                pass
        return all_ok
    except Exception as e:
        _LOGGER.error(
            "%s - async_unload_entry: Unload device failed: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False
