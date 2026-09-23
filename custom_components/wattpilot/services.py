"""Service actions for the Fronius Wattpilot integration.

Unlike the rest of the integration, which logs and degrades on failure, the
service handlers here **raise**: a service action is invoked by a user or an
automation, so a failure has to surface in the UI and stop the calling script
rather than disappear into the log (quality-scale rule ``action-exceptions``).

``ServiceValidationError`` reports a bad call — a missing parameter, an unknown
device, an unusable value. ``HomeAssistantError`` reports that the call was
valid but the charger could not carry it out.

Every raise carries a ``translation_key`` instead of a literal message, so the
text the user sees is localised (quality-scale rule ``exception-translations``).
The keys live under ``exceptions`` in ``strings.json`` and the files in
``translations/``; ``tests/test_exception_translations.py`` fails if the two
drift apart.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Any, Final, cast

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICE_ID, CONF_TRIGGER_TIME
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    CLOUD_API_URL_POSTFIX,
    CLOUD_API_URL_PREFIX,
    CONF_CLOUD_API,
    CONF_DAY_TYPE,
    CONF_DBG_PROPS,
    CONF_LIMIT_CHARGING_TIMES,
    CONF_PV_SURPLUS_OUTSIDE_TIMES,
    CONF_RANGES,
    DOMAIN,
)
from .schedule import (
    ATTR_BEGIN,
    ATTR_END,
    ATTR_LIMIT_CHARGING_TIMES,
    ATTR_PV_SURPLUS_OUTSIDE_TIMES,
    ATTR_RANGES,
    SCHEDULE_PROPS,
    ScheduleRangeError,
    build_schedule,
    decode_schedule,
    parse_time,
    validate_ranges,
)
from .utils import (
    async_ConnectCharger,
    async_GetChargerFromDeviceID,
    async_GetChargerProp,
    async_GetDataStoreFromDeviceID,
    async_SetChargerProp,
)

if TYPE_CHECKING:
    import datetime
    from collections.abc import Callable

    from wattpilot_api import Wattpilot

    from homeassistant.core import HomeAssistant, ServiceCall

    from .models import WattpilotRuntimeData

_LOGGER: Final = logging.getLogger(__name__)


def _fields_schema(*fields: str) -> vol.Schema:
    """Return a schema that accepts only the given fields, each of any value.

    The schema rejects a misspelt or unknown field before the handler runs. The
    fields' values are deliberately left to the handlers, which check them and
    raise a translated ``ServiceValidationError`` naming the parameter - a
    stricter schema would replace those messages with a generic one.
    """
    return vol.Schema({vol.Optional(field): cv.match_all for field in fields})


# The fields each service accepts, matching services.yaml.
SERVICE_SCHEMAS: Final[dict[str, vol.Schema]] = {
    "disconnect_charger": _fields_schema(CONF_DEVICE_ID),
    "reconnect_charger": _fields_schema(CONF_DEVICE_ID),
    "set_charging_schedule": _fields_schema(
        CONF_DAY_TYPE, CONF_DEVICE_ID, CONF_LIMIT_CHARGING_TIMES, CONF_PV_SURPLUS_OUTSIDE_TIMES, CONF_RANGES
    ),
    "set_debug_properties": _fields_schema(CONF_DBG_PROPS, CONF_DEVICE_ID),
    "set_goe_cloud": _fields_schema(CONF_CLOUD_API, CONF_DEVICE_ID),
    "set_next_trip": _fields_schema(CONF_DEVICE_ID, CONF_TRIGGER_TIME),
}


async def async_registerService(hass: HomeAssistant, name: str, service: Callable[..., Any]) -> None:
    """Register a service, with its schema from SERVICE_SCHEMAS, if it does not already exist."""
    try:
        _LOGGER.debug("%s - async_registerService: %s", DOMAIN, name)
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN, name, functools.partial(service, hass), schema=SERVICE_SCHEMAS.get(name)
            )
        else:
            _LOGGER.debug("%s - async_registerService: service already exists: %s", DOMAIN, name)
    except Exception as e:
        _LOGGER.exception(
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
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="missing_parameter",
            translation_placeholders={"parameter": key},
        )
    return value


def _ensure_entry_loaded(hass: HomeAssistant, device_id: str) -> None:
    """Refuse a call aimed at a charger whose config entry is not loaded.

    An entry that failed to set up, or was unloaded, can still hold the runtime
    data of its last setup, so resolving the charger would hand back a stale,
    disconnected client. An unknown device is left to the resolution helpers,
    which report it with their own errors.

    Args:
        hass: The Home Assistant instance.
        device_id: The device the service call targets.

    Raises:
        ServiceValidationError: If the device belongs to this integration but none
            of its config entries is loaded.
    """
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return
    entries = [
        entry
        for entry_id in device.config_entries
        if (entry := hass.config_entries.async_get_entry(entry_id)) is not None and entry.domain == DOMAIN
    ]
    if entries and not any(entry.state is ConfigEntryState.LOADED for entry in entries):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"device_id": str(device_id)},
        )


async def _async_get_charger(hass: HomeAssistant, device_id: str) -> Wattpilot:
    """Return the charger object behind a device id.

    Args:
        hass: The Home Assistant instance.
        device_id: The device the service call targets.

    Returns:
        The connected ``Wattpilot`` client for that device.

    Raises:
        ServiceValidationError: If the device's config entry is not loaded, or no
            charger can be resolved for the device.
    """
    _ensure_entry_loaded(hass, device_id)
    charger = await async_GetChargerFromDeviceID(hass, device_id)
    if not charger:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="charger_not_found",
            translation_placeholders={"device_id": str(device_id)},
        )
    return cast("Wattpilot", charger)


async def _async_get_entry_data(hass: HomeAssistant, device_id: str) -> WattpilotRuntimeData:
    """Return the runtime data store behind a device id.

    Args:
        hass: The Home Assistant instance.
        device_id: The device the service call targets.

    Returns:
        The config entry's runtime data.

    Raises:
        ServiceValidationError: If the device's config entry is not loaded, or no
            data store can be resolved for the device.
    """
    _ensure_entry_loaded(hass, device_id)
    entry_data = await async_GetDataStoreFromDeviceID(hass, device_id)
    if not entry_data:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"device_id": str(device_id)},
        )
    return cast("WattpilotRuntimeData", entry_data)


def _raise_service_failure(name: str, call: ServiceCall, e: Exception) -> HomeAssistantError:
    """Log an unexpected service failure and return the error to raise for it.

    Args:
        name: The handler name, used as the log context.
        call: The service call being processed.
        e: The unexpected exception.

    Returns:
        The ``HomeAssistantError`` the caller should raise from ``e``.
    """
    # This helper is called from the handlers rather than being one, so the
    # traceback is attached from the exception we were handed instead of via
    # .exception(), which would depend on an ambient sys.exc_info().
    _LOGGER.error(
        "%s - %s: %s failed: %s (%s.%s)",
        DOMAIN,
        name,
        call,
        str(e),
        e.__class__.__module__,
        type(e).__name__,
        exc_info=e,
    )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="service_failed",
        translation_placeholders={"service": call.service, "error": str(e)},
    )


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
        # The charger stores the departure time as seconds since its own local
        # midnight (the app's 06:00 reads back as ftt = 21600), and applies its own
        # time zone and daylight saving. So the value is plain clock arithmetic.
        # It used to be a 1970 timestamp built with time.mktime, which shifted it
        # by the Home Assistant host's UTC offset - ten hours, and negative, in
        # Australia - with an hour added back when tds == 1 that only cancelled
        # the error in central Europe.
        try:
            departure = parse_time(trigger_time)
        except ValueError as e:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_trigger_time",
                translation_placeholders={"parameter": CONF_TRIGGER_TIME, "trigger_time": str(trigger_time)},
            ) from e
        timestamp = departure.hour * 3600 + departure.minute * 60 + departure.second

        _LOGGER.debug(
            "%s - async_service_SetNextTrip: set nexttrip timestamp %s for charger: %s", DOMAIN, timestamp, charger.name
        )
        if not await async_SetChargerProp(charger, "ftt", timestamp):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_next_trip_failed",
                translation_placeholders={"charger": str(charger.name)},
            )
    except HomeAssistantError:
        raise
    except Exception as e:
        raise _raise_service_failure("async_service_SetNextTrip", call, e) from e


def _optional_bool(call: ServiceCall, key: str) -> bool | None:
    """Return an optional boolean service call parameter.

    Args:
        call: The service call to read from.
        key: The name of the parameter.

    Returns:
        The parameter as a bool, or None when it was not supplied.

    Raises:
        ServiceValidationError: If the value is neither a bool nor "true"/"false".
    """
    value = call.data.get(key, None)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_boolean",
        translation_placeholders={"parameter": key, "value": str(value)},
    )


def _schedule_ranges(call: ServiceCall) -> list[tuple[datetime.time, datetime.time]] | None:
    """Return the charging windows of a set_charging_schedule call.

    Args:
        call: The service call to read from.

    Returns:
        The windows as (begin, end) times, or None when ``ranges`` was not supplied.

    Raises:
        ServiceValidationError: If ``ranges`` is not a list of ``{begin, end}``
            entries with times in ``HH:MM`` or ``HH:MM:SS`` form, a window does
            not end after it begins on the same day, or two windows overlap.
    """
    ranges = call.data.get(CONF_RANGES, None)
    if ranges is None:
        return None
    invalid = ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_schedule_ranges",
        translation_placeholders={"parameter": CONF_RANGES, "value": str(ranges)},
    )
    if not isinstance(ranges, list):
        raise invalid
    parsed: list[tuple[datetime.time, datetime.time]] = []
    for item in ranges:
        if not isinstance(item, dict) or ATTR_BEGIN not in item or ATTR_END not in item:
            raise invalid
        try:
            parsed.append((parse_time(item[ATTR_BEGIN]), parse_time(item[ATTR_END])))
        except ValueError as e:
            raise invalid from e
    # Reject a window that would spill into the next day type, or windows that
    # overlap, before anything reaches the charger.
    try:
        validate_ranges(parsed)
    except ScheduleRangeError as e:
        # Two literal raises rather than one with a computed key: the
        # translation guard in tests reads keys and placeholders from the source.
        if e.reason == "crosses_midnight":
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="schedule_range_crosses_midnight",
                translation_placeholders={"parameter": CONF_RANGES, "ranges": ", ".join(e.ranges)},
            ) from e
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="schedule_ranges_overlap",
            translation_placeholders={"parameter": CONF_RANGES, "ranges": ", ".join(e.ranges)},
        ) from e
    return parsed


async def async_service_SetChargingSchedule(hass: HomeAssistant, call: ServiceCall) -> None:
    """Write one day type of the app's charging schedule to the charger.

    The schedule property is a nested object the charger cannot take partial
    writes for, so the call reads the current schedule and rewrites the whole
    object with the supplied fields changed. A field that is not supplied keeps
    its current value.

    Args:
        hass: The Home Assistant instance.
        call: The service call, carrying ``device_id``, ``day_type`` and any of
            ``limit_charging_times``, ``pv_surplus_outside_times`` and ``ranges``.

    Raises:
        ServiceValidationError: If a parameter is missing or unusable, the device
            is unknown, the charger has no schedule property, or nothing was
            supplied to change.
        HomeAssistantError: If the schedule could not be written to the charger.
    """
    try:
        device_id = _required(call, CONF_DEVICE_ID)
        day_type = str(_required(call, CONF_DAY_TYPE)).lower()
        prop = SCHEDULE_PROPS.get(day_type)
        if prop is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_day_type",
                translation_placeholders={
                    "parameter": CONF_DAY_TYPE,
                    "value": day_type,
                    "options": ", ".join(SCHEDULE_PROPS),
                },
            )
        limit_times = _optional_bool(call, CONF_LIMIT_CHARGING_TIMES)
        pv_surplus = _optional_bool(call, CONF_PV_SURPLUS_OUTSIDE_TIMES)
        ranges = _schedule_ranges(call)
        if limit_times is None and pv_surplus is None and ranges is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="schedule_nothing_to_set",
                translation_placeholders={
                    "parameters": ", ".join((CONF_LIMIT_CHARGING_TIMES, CONF_PV_SURPLUS_OUTSIDE_TIMES, CONF_RANGES))
                },
            )

        _LOGGER.debug("%s - async_service_SetChargingSchedule: get charger for device_id: %s", DOMAIN, device_id)
        charger = await _async_get_charger(hass, device_id)

        current = decode_schedule(await async_GetChargerProp(charger, prop))
        if current is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="schedule_not_supported",
                translation_placeholders={"charger": str(charger.name), "property": prop},
            )
        if ranges is None:
            ranges = [(parse_time(item[ATTR_BEGIN]), parse_time(item[ATTR_END])) for item in current[ATTR_RANGES]]
        schedule = build_schedule(
            current[ATTR_LIMIT_CHARGING_TIMES] if limit_times is None else limit_times,
            current[ATTR_PV_SURPLUS_OUTSIDE_TIMES] if pv_surplus is None else pv_surplus,
            ranges,
        )

        _LOGGER.debug(
            "%s - async_service_SetChargingSchedule: set %s for charger %s: %s", DOMAIN, prop, charger.name, schedule
        )
        if not await async_SetChargerProp(charger, prop, schedule):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_schedule_failed",
                translation_placeholders={"charger": str(charger.name), "day_type": day_type},
            )
    except HomeAssistantError:
        raise
    except Exception as e:
        raise _raise_service_failure("async_service_SetChargingSchedule", call, e) from e


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
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cloud_api_enable_failed",
                    translation_placeholders={"charger": str(charger.name)},
                )
            timer = 0
            timeout = 10
            while timeout > timer and (charger.cak == "" or charger.cak is None):
                await asyncio.sleep(1)
                timer += 1
            if not timeout > timer:
                entry_data.cloud_api_key = False
                # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- reports only the timeout duration, never the key  # noqa: E501
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cloud_api_key_timeout",
                    translation_placeholders={"timeout": str(timeout)},
                )

            _LOGGER.debug("%s - async_service_SetGoECloud: Saving api key to data store", DOMAIN)
            entry_data.cloud_api_key = charger.cak
            api_key = str(charger.cak) if charger.cak is not None else ""
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
                entry_data.cloud_api_url = CLOUD_API_URL_PREFIX + serial + CLOUD_API_URL_POSTFIX
                _LOGGER.info(
                    "%s - async_service_SetGoECloud: %s cloud API URL: %s",
                    DOMAIN,
                    charger.name,
                    entry_data.cloud_api_url,
                )
        else:
            _LOGGER.debug("%s - async_service_SetGoECloud: %s disabling cloud api", DOMAIN, charger.name)
            entry_data.cloud_api_key = False
            if not await async_SetChargerProp(charger, "cae", False):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cloud_api_disable_failed",
                    translation_placeholders={"charger": str(charger.name)},
                )
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
            entry_data.debug_properties = dbg_state
        elif isinstance(dbg_state, str) and dbg_state.lower() == "true":
            entry_data.debug_properties = True
        elif isinstance(dbg_state, str) and dbg_state.lower() == "false":
            entry_data.debug_properties = False
        elif isinstance(dbg_state, list):
            entry_data.debug_properties = [str(prop) for prop in dbg_state]
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_debug_properties",
                translation_placeholders={"parameter": CONF_DBG_PROPS, "value": str(dbg_state)},
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
        reconnected = await async_ConnectCharger(device_id, entry_data.params, charger)
        if reconnected is False:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="reconnect_failed",
                translation_placeholders={"device_id": str(device_id)},
            )
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
