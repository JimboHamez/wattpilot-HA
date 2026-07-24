"""Service actions for the Fronius Wattpilot integration.

Unlike the rest of the integration, which logs and degrades on failure, the
service handlers here **raise**: a service action is invoked by a user or an
automation, so a failure has to surface in the UI and stop the calling script
rather than disappear into the log (quality-scale rule ``action-exceptions``).

``ServiceValidationError`` reports a bad call — a missing parameter, an unknown
device, an unusable value. ``HomeAssistantError`` reports that the call was
valid but the charger could not carry it out.
"""

from __future__ import annotations

import asyncio
import datetime
import functools
import logging
import time
from typing import TYPE_CHECKING, Any, Final, cast

from homeassistant.const import CONF_API_KEY, CONF_DEVICE_ID, CONF_EXTERNAL_URL, CONF_PARAMS, CONF_TRIGGER_TIME
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import CLOUD_API_URL_POSTFIX, CLOUD_API_URL_PREFIX, CONF_CLOUD_API, CONF_DBG_PROPS, DOMAIN
from .utils import (
    async_ConnectCharger,
    async_GetChargerFromDeviceID,
    async_GetChargerProp,
    async_GetDataStoreFromDeviceID,
    async_SetChargerProp,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from wattpilot_api import Wattpilot

    from homeassistant.core import HomeAssistant, ServiceCall

_LOGGER: Final = logging.getLogger(__name__)


async def async_registerService(hass: HomeAssistant, name: str, service: Callable[..., Any]) -> None:
    """Register a service if it does not already exist."""
    try:
        _LOGGER.debug("%s - async_registerService: %s", DOMAIN, name)
        await asyncio.sleep(0)
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, functools.partial(service, hass))
        else:
            _LOGGER.debug("%s - async_registerServic: service already exists: %s", DOMAIN, name)
    except Exception as e:
        _LOGGER.error(
            "%s - async_registerService: failed: %s (%s.%s)", DOMAIN, str(e), e.__class__.__module__, type(e).__name__
        )


def _required(call: ServiceCall, key: str) -> Any:
    """Return a required service call parameter.

    Args:
        call: The service call to read from.
        key: The name of the required parameter.

    Returns:
        The parameter value.

    Raises:
        ServiceValidationError: If the parameter was not supplied.
    """
    value = call.data.get(key, None)
    if value is None:
        raise ServiceValidationError(f"{key} is a required parameter")
    return value


async def _async_get_charger(hass: HomeAssistant, device_id: str) -> Wattpilot:
    """Return the charger object behind a device id.

    Args:
        hass: The Home Assistant instance.
        device_id: The device the service call targets.

    Returns:
        The connected ``Wattpilot`` client for that device.

    Raises:
        ServiceValidationError: If no charger can be resolved for the device.
    """
    charger = await async_GetChargerFromDeviceID(hass, device_id)
    if not charger:
        raise ServiceValidationError(f"Unable to identify a Wattpilot charger for device: {device_id}")
    return cast("Wattpilot", charger)


async def _async_get_entry_data(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    """Return the runtime data store behind a device id.

    Args:
        hass: The Home Assistant instance.
        device_id: The device the service call targets.

    Returns:
        The config entry's runtime data dict.

    Raises:
        ServiceValidationError: If no data store can be resolved for the device.
    """
    entry_data = await async_GetDataStoreFromDeviceID(hass, device_id)
    if not entry_data:
        raise ServiceValidationError(f"Unable to identify the Wattpilot config entry for device: {device_id}")
    return cast("dict[str, Any]", entry_data)


def _raise_service_failure(name: str, call: ServiceCall, e: Exception) -> HomeAssistantError:
    """Log an unexpected service failure and return the error to raise for it.

    Args:
        name: The handler name, used as the log context.
        call: The service call being processed.
        e: The unexpected exception.

    Returns:
        The ``HomeAssistantError`` the caller should raise from ``e``.
    """
    _LOGGER.error(
        "%s - %s: %s failed: %s (%s.%s)", DOMAIN, name, call, str(e), e.__class__.__module__, type(e).__name__
    )
    return HomeAssistantError(f"Wattpilot service {call.service} failed: {e}")


async def async_service_SetNextTrip(hass: HomeAssistant, call: ServiceCall) -> None:
    """Write the next-trip departure timestamp to the charger.

    Args:
        hass: The Home Assistant instance.
        call: The service call, carrying ``device_id`` and ``trigger_time``.

    Raises:
        ServiceValidationError: If a parameter is missing, the device is unknown,
            or the trigger time cannot be parsed.
        HomeAssistantError: If the timestamp could not be written to the charger.
    """
    try:
        device_id = _required(call, CONF_DEVICE_ID)
        trigger_time = _required(call, CONF_TRIGGER_TIME)

        _LOGGER.debug("%s - async_service_SetNextTrip: get charger for device_id: %s", DOMAIN, device_id)
        charger = await _async_get_charger(hass, device_id)

        _LOGGER.debug("%s - async_service_SetNextTrip: trigger time: %s", DOMAIN, trigger_time)
        try:
            timestamp = int(
                time.mktime(datetime.datetime.strptime("1970-01-01 " + trigger_time, "%Y-%m-%d %H:%M:%S").timetuple())
            )
        except (TypeError, ValueError) as e:
            raise ServiceValidationError(f"{CONF_TRIGGER_TIME} is not a valid time: {trigger_time}") from e

        _LOGGER.debug("%s - async_service_SetNextTrip: validate daylight saving", DOMAIN)
        tds = await async_GetChargerProp(charger, "tds")
        if tds is not None and int(tds) == 1:
            _LOGGER.debug("%s - async_service_SetNextTrip: apply daylight saving time", DOMAIN)
            timestamp = timestamp + 3600

        _LOGGER.debug(
            "%s - async_service_SetNextTrip: set nexttrip timestamp %s for charger: %s", DOMAIN, timestamp, charger.name
        )
        if not await async_SetChargerProp(charger, "ftt", timestamp):
            raise HomeAssistantError(f"Unable to set the next trip timestamp on charger: {charger.name}")
    except HomeAssistantError:
        raise
    except Exception as e:
        raise _raise_service_failure("async_service_SetNextTrip", call, e) from e


async def async_service_SetGoECloud(hass: HomeAssistant, call: ServiceCall) -> None:
    """Enable or disable the go-e cloud API and cache the returned key/URL.

    Args:
        hass: The Home Assistant instance.
        call: The service call, carrying ``device_id`` and ``cloud_api``.

    Raises:
        ServiceValidationError: If a parameter is missing or the device is unknown.
        HomeAssistantError: If the charger rejected the change or returned no API
            key within the timeout.
    """
    try:
        device_id = _required(call, CONF_DEVICE_ID)
        api_state = _required(call, CONF_CLOUD_API)
        _LOGGER.debug("%s - async_service_SetGoECloud: service call data: %s", DOMAIN, call.data)

        _LOGGER.debug("%s - async_service_SetGoECloud: get entry_data for device_id: %s", DOMAIN, device_id)
        entry_data = await _async_get_entry_data(hass, device_id)

        _LOGGER.debug("%s - async_service_SetGoECloud: get charger for device_id: %s", DOMAIN, device_id)
        charger = await _async_get_charger(hass, device_id)

        if api_state is True:
            _LOGGER.debug("%s - async_service_SetGoECloud: Enabling cloud api", DOMAIN)
            if not await async_SetChargerProp(charger, "cae", True):
                raise HomeAssistantError(f"Unable to enable the go-e cloud API on charger: {charger.name}")
            timer = 0
            timeout = 10
            while timeout > timer and (charger.cak == "" or charger.cak is None):
                await asyncio.sleep(1)
                timer += 1
            if not timeout > timer:
                entry_data[CONF_API_KEY] = False
                # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- reports only the timeout duration, never the key  # noqa: E501
                raise HomeAssistantError(f"The charger returned no go-e cloud API key within {timeout} seconds")

            _LOGGER.debug("%s - async_service_SetGoECloud: Saving api key to data store", DOMAIN)
            entry_data[CONF_API_KEY] = charger.cak
            api_key = str(entry_data[CONF_API_KEY]) if entry_data[CONF_API_KEY] is not None else ""
            # Log only whether a key is present and its length, never the key itself.
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- deliberately logs presence/length only  # noqa: E501
            _LOGGER.debug(
                "%s - async_service_SetGoECloud: %s cloud API key stored (present=%s, length=%s)",
                DOMAIN,
                charger.name,
                bool(api_key),
                len(api_key),
            )

            serial = getattr(charger, "serial", await async_GetChargerProp(charger, "sse", False))
            if serial:
                entry_data[CONF_EXTERNAL_URL] = CLOUD_API_URL_PREFIX + serial + CLOUD_API_URL_POSTFIX
                _LOGGER.info(
                    "%s - async_service_SetGoECloud: %s cloud API URL: %s",
                    DOMAIN,
                    charger.name,
                    entry_data[CONF_EXTERNAL_URL],
                )
        else:
            _LOGGER.debug("%s - async_service_SetGoECloud: %s disabling cloud api", DOMAIN, charger.name)
            entry_data[CONF_API_KEY] = False
            if not await async_SetChargerProp(charger, "cae", False):
                raise HomeAssistantError(f"Unable to disable the go-e cloud API on charger: {charger.name}")
            _LOGGER.info("%s - async_service_SetGoECloud: %s DISABLED cloud API", DOMAIN, charger.name)
    except HomeAssistantError:
        raise
    except Exception as e:
        raise _raise_service_failure("async_service_SetGoECloud", call, e) from e


async def async_service_SetDebugProperties(hass: HomeAssistant, call: ServiceCall) -> None:
    """Enable or disable property-change debug logging for a charger.

    Args:
        hass: The Home Assistant instance.
        call: The service call, carrying ``device_id`` and ``debug_properties``.

    Raises:
        ServiceValidationError: If a parameter is missing, the device is unknown,
            or the debug state is neither a bool, a bool-like string nor a list.
        HomeAssistantError: If the setting could not be stored.
    """
    try:
        device_id = _required(call, CONF_DEVICE_ID)
        dbg_state = _required(call, CONF_DBG_PROPS)

        _LOGGER.debug("%s - async_service_SetDebugProperties: get entry_data for device_id: %s", DOMAIN, device_id)
        entry_data = await _async_get_entry_data(hass, device_id)

        if isinstance(dbg_state, bool):
            entry_data[CONF_DBG_PROPS] = dbg_state
        elif isinstance(dbg_state, str) and dbg_state.lower() == "true":
            entry_data[CONF_DBG_PROPS] = True
        elif isinstance(dbg_state, str) and dbg_state.lower() == "false":
            entry_data[CONF_DBG_PROPS] = False
        elif isinstance(dbg_state, list):
            entry_data[CONF_DBG_PROPS] = dbg_state
        else:
            raise ServiceValidationError(
                f"{CONF_DBG_PROPS} must be true, false or a list of property names, got: {dbg_state}"
            )
    except HomeAssistantError:
        raise
    except Exception as e:
        raise _raise_service_failure("async_service_SetDebugProperties", call, e) from e


async def async_service_ReConnectCharger(hass: HomeAssistant, call: ServiceCall) -> None:
    """Disconnect (if needed) and reconnect the charger's WebSocket session.

    Args:
        hass: The Home Assistant instance.
        call: The service call, carrying ``device_id``.

    Raises:
        ServiceValidationError: If ``device_id`` is missing or the device is unknown.
        HomeAssistantError: If the charger could not be reconnected.
    """
    try:
        device_id = _required(call, CONF_DEVICE_ID)
        _LOGGER.debug("%s - async_service_ReConnectCharger: service call data: %s", DOMAIN, call.data)

        _LOGGER.debug("%s - async_service_ReConnectCharger: get entry_data for device_id: %s", DOMAIN, device_id)
        entry_data = await _async_get_entry_data(hass, device_id)

        _LOGGER.debug("%s - async_service_ReConnectCharger: get charger for device_id: %s", DOMAIN, device_id)
        charger = await _async_get_charger(hass, device_id)

        if charger.connected:
            _LOGGER.debug("%s - async_service_ReConnectCharger: first disconnect charger: %s", DOMAIN, device_id)
            await async_service_DisconnectCharger(hass, call)
            await asyncio.sleep(1)

        _LOGGER.debug("%s - async_service_ReConnectCharger: Connecting charger", DOMAIN)
        # The existing charger object is reused, so entities and the connection
        # monitor keep pointing at the reconnected session.
        reconnected = await async_ConnectCharger(device_id, entry_data[CONF_PARAMS], charger)
        if reconnected is False:
            raise HomeAssistantError(f"Unable to reconnect the Wattpilot charger for device: {device_id}")
        _LOGGER.info("%s - async_service_ReConnectCharger: Charger reconnected: %s", DOMAIN, reconnected.name)
    except HomeAssistantError:
        raise
    except Exception as e:
        raise _raise_service_failure("async_service_ReConnectCharger", call, e) from e


async def async_service_DisconnectCharger(hass: HomeAssistant, call: ServiceCall) -> None:
    """Close the charger's WebSocket session (helpful for the Wattpilot GO).

    Args:
        hass: The Home Assistant instance.
        call: The service call, carrying ``device_id``.

    Raises:
        ServiceValidationError: If ``device_id`` is missing or the device is unknown.
        HomeAssistantError: If the session could not be closed.
    """
    try:
        device_id = _required(call, CONF_DEVICE_ID)
        _LOGGER.debug("%s - async_service_DisconnectCharger: service call data: %s", DOMAIN, call.data)

        _LOGGER.debug("%s - async_service_DisconnectCharger: get charger for device_id: %s", DOMAIN, device_id)
        charger = await _async_get_charger(hass, device_id)

        await charger.disconnect()
        _LOGGER.info("%s - async_service_DisconnectCharger: Charger disconnected: %s", DOMAIN, charger.name)
    except HomeAssistantError:
        raise
    except Exception as e:
        raise _raise_service_failure("async_service_DisconnectCharger", call, e) from e
