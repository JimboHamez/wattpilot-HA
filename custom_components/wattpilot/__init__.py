"""Init for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, Literal

from wattpilot_api.exceptions import AuthenticationError

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.loader import async_get_integration

from .availability import ChargerConnectionMonitor
from .const import AUTH_FAILURE_REAUTH_THRESHOLD, DOMAIN, SUPPORTED_PLATFORMS
from .models import WattpilotRuntimeData
from .services import (
    async_registerService,
    async_service_DisconnectCharger,
    async_service_ReConnectCharger,
    async_service_SetChargingSchedule,
    async_service_SetDebugProperties,
    async_service_SetGoECloud,
    async_service_SetNextTrip,
)
from .utils import (
    async_ConnectCharger,
    async_DisconnectCharger,
    async_PreloadApiDefinition,
    async_PropertyUpdateHandler,
)

if TYPE_CHECKING:
    from wattpilot_api import Wattpilot

    from homeassistant.core import HomeAssistant

    from .models import WattpilotConfigEntry

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
        await async_registerService(hass, "set_charging_schedule", async_service_SetChargingSchedule)
        await async_registerService(hass, "set_goe_cloud", async_service_SetGoECloud)
        await async_registerService(hass, "set_debug_properties", async_service_SetDebugProperties)
        await async_registerService(hass, "set_next_trip", async_service_SetNextTrip)
    except Exception as e:
        _LOGGER.exception(
            "%s - async_setup: register services failed: %s (%s.%s)",
            DOMAIN,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WattpilotConfigEntry) -> bool:
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
        _LOGGER.exception(
            "%s - async_setup_entry: Connecting charger failed: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await async_DisconnectCharger(entry.entry_id, charger)
        raise ConfigEntryNotReady(f"Error connecting to Wattpilot charger for entry {entry.entry_id}: {e}") from e

    # Give the charger its API definition, read in an executor, so that its first
    # write does not have to read it from disk on the event loop.
    await async_PreloadApiDefinition(hass, entry.entry_id, charger)

    try:
        _LOGGER.debug("%s - async_setup_entry: Creating runtime data store for %s", entry.entry_id, DOMAIN)
        entry.runtime_data = WattpilotRuntimeData(charger=charger, params=entry.data)
        entry_data = entry.runtime_data
    except Exception as e:
        _LOGGER.exception(
            "%s - async_setup_entry: Creating data store failed: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await async_DisconnectCharger(entry.entry_id, charger)
        raise ConfigEntryError(f"Creating the data store failed for entry {entry.entry_id}: {e}") from e

    # Everything that is not a platform is wired up before the platforms are
    # forwarded, so a failing step only has this entry's own resources to release
    # and never has to unload platforms from inside setup. A property pushed before
    # the entities exist reaches no subscriber, which is harmless: each entity
    # seeds its state from the charger when it is added.
    try:
        _LOGGER.debug("%s - async_setup_entry: register properties update handler", entry.entry_id)

        # The wattpilot_api client fires property callbacks on Home Assistant's
        # own event loop, so an async callback can be registered directly.
        # on_property_change returns an unsubscribe function used on unload.
        async def _property_update_callback(identifier: str, value: Any) -> None:
            await async_PropertyUpdateHandler(hass, entry, identifier, value)

        entry_data.property_updates_unsub = charger.on_property_change(_property_update_callback)
    except Exception as e:
        _LOGGER.exception(
            "%s - async_setup_entry: Could not register properties updater handler: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await _async_release_charger(entry.entry_id, entry_data)
        raise ConfigEntryError(f"Registering the property handler failed for entry {entry.entry_id}: {e}") from e

    try:
        _LOGGER.debug("%s - async_setup_entry: start charger connection monitor", entry.entry_id)
        entry_data.connection_monitor_cancel = ChargerConnectionMonitor(hass, entry.entry_id, charger).async_start()
    except Exception as e:
        _LOGGER.exception(
            "%s - async_setup_entry: Could not start charger connection monitor: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await _async_release_charger(entry.entry_id, entry_data)
        raise ConfigEntryError(f"Starting the connection monitor failed for entry {entry.entry_id}: {e}") from e

    try:
        _LOGGER.debug("%s - async_setup_entry: Trigger setup for platforms", entry.entry_id)
        await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)
    except Exception as e:
        _LOGGER.exception(
            "%s - async_setup_entry: Setup trigger failed: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        await _async_release_charger(entry.entry_id, entry_data)
        raise ConfigEntryError(f"Setting up the platforms failed for entry {entry.entry_id}: {e}") from e

    _LOGGER.debug("%s - async_setup_entry: Completed", entry.entry_id)
    return True


async def _async_release_charger(entry_id: str, entry_data: WattpilotRuntimeData) -> None:
    """Stop the connection monitor, unsubscribe from the charger and disconnect it.

    Shared by unload and by a setup that fails after connecting. Each step is
    attempted on its own, so one failing step does not leave the others undone.

    Args:
        entry_id: The config entry id, used as the log prefix.
        entry_data: The entry's runtime data store.
    """
    try:
        _LOGGER.debug("%s - _async_release_charger: stop charger connection monitor", entry_id)
        if entry_data.connection_monitor_cancel is not None:
            entry_data.connection_monitor_cancel()
            entry_data.connection_monitor_cancel = None
    except Exception as e:
        _LOGGER.exception(
            "%s - _async_release_charger: failed to stop charger connection monitor: %s (%s.%s)",
            entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )

    try:
        _LOGGER.debug("%s - _async_release_charger: remove registered event handlers", entry_id)
        if entry_data.property_updates_unsub is not None:
            entry_data.property_updates_unsub()
            entry_data.property_updates_unsub = None
    except Exception as e:
        _LOGGER.exception(
            "%s - _async_release_charger: failed to remove registered event handlers: %s (%s.%s)",
            entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )

    try:
        await async_DisconnectCharger(entry_id, entry_data.charger)
    except Exception as e:
        _LOGGER.exception(
            "%s - _async_release_charger: could not disconnect charger, its session may stay open until the "
            "charger restarts: %s (%s.%s)",
            entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )


async def async_unload_entry(hass: HomeAssistant, entry: WattpilotConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        _LOGGER.debug("Unloading config entry: %s", entry.entry_id)
        _AUTH_FAILURE_COUNTS.pop(entry.entry_id, None)
        # async_unload_platforms returns False if at least one platform did not unload.
        unload_ok = await hass.config_entries.async_unload_platforms(entry, SUPPORTED_PLATFORMS)
        if not unload_ok:
            _LOGGER.error("%s - async_unload_entry: failed to unload platforms", entry.entry_id)
            return False
        await _async_release_charger(entry.entry_id, entry.runtime_data)
        return True
    except Exception as e:
        _LOGGER.exception(
            "%s - async_unload_entry: Unload device failed: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False
