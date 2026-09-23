"""Helper functions for Fronius Wattpilot."""

from __future__ import annotations

import json
import logging
import types
from functools import cache
from typing import TYPE_CHECKING, Any, Final, Literal

from wattpilot_api import Wattpilot
from wattpilot_api.definition import load_api_definition
from wattpilot_api.exceptions import AuthenticationError, WattpilotError

from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_CLOUD,
    CONF_CONNECTION,
    CONF_LOCAL,
    CONF_SERIAL,
    DEFAULT_NAME,
    DEFAULT_TIMEOUT,
    DOMAIN,
    EVENT_PROPS,
    EVENT_PROPS_ID,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from wattpilot_api.definition import ApiDefinition

    from homeassistant.core import HomeAssistant

    from .models import WattpilotConfigEntry, WattpilotRuntimeData

_LOGGER: Final = logging.getLogger(__name__)


@cache
def _load_api_definition() -> ApiDefinition | None:
    """Read the client's API definition. Blocking - call it from an executor.

    Every write coerces its value against ``wattpilot.yaml``, which the client
    otherwise reads from disk on its first write - blocking, on the event loop, which
    Home Assistant reports. Reading it here instead keeps that work off the loop, and
    the cache means one parse serves every charger. A failed read is cached too: the
    file ships with the client, so a second attempt would fail the same way, and the
    client is left to load its own definition. See ``async_PreloadApiDefinition``.

    Returns:
        The parsed definition, or ``None`` if it could not be read.
    """
    try:
        # split_properties=False mirrors the client's own call, so what it coerces
        # against is exactly what it would have loaded.
        return load_api_definition(split_properties=False)
    except Exception as e:
        _LOGGER.exception(
            "%s - _load_api_definition: Reading the API definition failed: %s (%s.%s)",
            DOMAIN,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return None


def property_update_signal(entry_id: str, identifier: str) -> str:
    """Return the dispatcher signal for a charger property update."""
    return f"{DOMAIN}_{entry_id}_property_{identifier}"


async def async_ProgrammingDebug(obj: object, show_all: bool = False) -> None:
    """Async: return all attributes of a specific object."""
    try:
        _LOGGER.debug("%s - async_ProgrammingDebug: %s", DOMAIN, obj)
        for attr in dir(obj):
            if attr.startswith("_") and not show_all:
                continue
            if hasattr(obj, attr):
                _LOGGER.debug("%s - async_ProgrammingDebug: %s = %s", DOMAIN, attr, getattr(obj, attr))
    except Exception as e:
        _LOGGER.exception(
            "%s - async_ProgrammingDebug: failed: %s (%s.%s)", DOMAIN, str(e), e.__class__.__module__, type(e).__name__
        )
        pass


def ProgrammingDebug(obj: object, show_all: bool = False) -> None:
    """Return all attributes of a specific object."""
    try:
        _LOGGER.debug("%s - ProgrammingDebug: %s", DOMAIN, obj)
        for attr in dir(obj):
            if attr.startswith("_") and not show_all:
                continue
            if hasattr(obj, attr):
                _LOGGER.debug("%s - ProgrammingDebug: %s = %s", DOMAIN, attr, getattr(obj, attr))
    except Exception as e:
        _LOGGER.exception(
            "%s - ProgrammingDebug: failed: %s (%s.%s)", DOMAIN, str(e), e.__class__.__module__, type(e).__name__
        )
        pass


async def async_PropertyDebug(identifier: str, value: str, include_properties: bool | list[str]) -> None:
    """Log properties if they change."""
    exclude_properties = [
        "efh",
        "efh32",
        "efh8",
        "ehs",
        "emhb",
        "fbuf_age",
        "fbuf_pAkku",
        "fbuf_pGrid",
        "fbuf_pPv",
        "fhz",
        "loc",
        "lps",
        "nrg",
        "rbt",
        "rcd",
        "rfb",
        "rssi",
        "tma",
        "tpcm",
        "utc",
        "fbuf_akkuSOC",
        "lpsc",
        "pvopt_averagePAkku",
        "pvopt_averagePGrid",
        "pvopt_averagePPv",
        "pvopt_deltaP",
    ]
    if (isinstance(include_properties, list) and identifier in include_properties) or (
        isinstance(include_properties, bool) and identifier not in exclude_properties
    ):
        _LOGGER.warning("async_PropertyDebug: watch_properties: %s => %s ", identifier, value)


async def async_PropertyUpdateHandler(
    hass: HomeAssistant, entry: WattpilotConfigEntry, identifier: str, value: str
) -> None:
    """Async: dispatch a charger property update to the subscribed entities."""
    try:
        entry_data = entry.runtime_data
        # Entities subscribe per property in async_added_to_hass; route the
        # update to them via the dispatcher signal for this (entry, property).
        async_dispatcher_send(hass, property_update_signal(entry.entry_id, identifier), value)

        if identifier in EVENT_PROPS:
            params = entry_data.params
            charger_id = str(params.get(CONF_FRIENDLY_NAME, params.get(CONF_IP_ADDRESS, DEFAULT_NAME)))
            data = {"charger_id": charger_id, "entry_id": entry.entry_id, "property": identifier, "value": value}
            # The client calls back on the event loop, so the loop-only variant applies.
            hass.bus.async_fire(EVENT_PROPS_ID, data)

        if entry_data.debug_properties:
            hass.async_create_task(async_PropertyDebug(identifier, value, entry_data.debug_properties))
    except Exception as e:
        _LOGGER.exception(
            "%s - async_PropertyUpdateHandler: Could not 'self' execute async: %s (%s.%s)",
            entry.entry_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return None


async def async_GetChargerProp(charger: Wattpilot, identifier: str, default: Any = None) -> Any:
    """Async: return the value of a charger attribute."""
    try:
        if not hasattr(charger, "all_properties"):
            _LOGGER.error(
                "%s - async_GetChargerProp: Charger does not have all_properties attribute: %s", DOMAIN, charger
            )
            return default
        if identifier is None or identifier not in charger.all_properties:
            # Not an error: the caller supplies a default and handles absence
            # (e.g. optional/firmware-dependent properties like 'cards'). Logging
            # at error level here spams once per poll for every absent property.
            _LOGGER.debug("%s - async_GetChargerProp: Charger does not have property: %s", DOMAIN, identifier)
            return default
        if charger.all_properties[identifier] is None and default is not None:
            return default
        return charger.all_properties[identifier]
    except Exception as e:
        _LOGGER.exception(
            "%s - async_GetChargerProp: Could not get property %s: %s (%s.%s)",
            DOMAIN,
            identifier,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return default


def GetChargerProp(charger: Wattpilot, identifier: str | None = None, default: Any = None) -> Any:
    """Return the value of a charger attribute."""
    try:
        if not hasattr(charger, "all_properties"):
            _LOGGER.error("%s - GetChargerProp: Charger does not have all_properties attribute: %s", DOMAIN, charger)
            return default
        if identifier is None or identifier not in charger.all_properties:
            # Not an error: the caller supplies a default and handles absence
            # (e.g. optional/firmware-dependent properties like 'cards'). Logging
            # at error level here spams once per poll for every absent property.
            _LOGGER.debug("%s - GetChargerProp: Charger does not have property: %s", DOMAIN, identifier)
            return default
        if charger.all_properties[identifier] is None and default is not None:
            return default
        return charger.all_properties[identifier]
    except Exception as e:
        _LOGGER.exception(
            "%s - GetChargerProp: Could not get property %s: %s (%s.%s)",
            DOMAIN,
            identifier,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return default


async def async_SetChargerProp(
    charger: Wattpilot,
    identifier: str | None = None,
    value: Any = None,
    force: bool = False,
    force_type: str | None = None,
) -> bool:
    """Async: set the value of a charger attribute."""
    try:
        if not hasattr(charger, "all_properties"):
            _LOGGER.error(
                "%s - async_SetChargerProp: Charger does not have all_properties attribute: %s", DOMAIN, charger
            )
            return False
        if identifier is None:
            _LOGGER.error("%s - async_SetChargerProp: Charger property name has to be defined: %s", DOMAIN, identifier)
            return False
        if identifier not in charger.all_properties and not force:
            _LOGGER.error("%s - async_SetChargerProp: Charger does not have property: %s", DOMAIN, identifier)
            return False
        if value is None:
            _LOGGER.error("%s - async_SetChargerProp: A value parameter is required: %s=%s", DOMAIN, identifier, value)
            return False

        if force_type is not None:
            force_type = str(force_type).lower()

        # Coerce the value to the JSON type the charger expects. Order matters:
        # an explicit force_type wins, then bool (so "true"/"false" never fall
        # through to string), then int, then float, with str as the fallback.
        # SimpleNamespace values (e.g. the 'cll' current-limit object) are sent
        # as their underlying dict, and a dict or list (a charging schedule
        # built by the service) is already the JSON value to send.
        _LOGGER.debug("%s - async_SetChargerProp: Prepare new property value: %s=%s", DOMAIN, identifier, value)
        v: Any
        if isinstance(value, (dict, list)):
            v = value
        elif force_type == "str":
            v = str(value)
        elif str(value).lower() in ["false", "true"] or force_type == "bool":
            v = json.loads(str(value).lower())
        elif str(value).isnumeric() or force_type == "int":
            v = int(value)
        elif str(value).isdecimal() or force_type == "float":
            v = float(value)
        elif type(value) is types.SimpleNamespace:
            _LOGGER.warning(
                "%s - async_SetChargerProp: Set for namespace detected - this is untested: %s=%s",
                DOMAIN,
                identifier,
                value,
            )
            v = value.__dict__
        else:
            v = str(value)

        _LOGGER.debug("%s - async_SetChargerProp: Send property update to charger: %s=%s", DOMAIN, identifier, v)
        await charger.set_property(identifier, v)
        return True
    except Exception as e:
        _LOGGER.exception(
            "%s - async_SetChargerProp: Could not set property %s: %s (%s.%s)",
            DOMAIN,
            identifier,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False


def _runtime_data_for_device(hass: HomeAssistant, device_id: str) -> WattpilotRuntimeData | None:
    """Return the runtime data of the first set-up Wattpilot entry behind a device.

    Only this integration's entries are considered: a device can also belong to
    other integrations' entries, whose runtime data is something else entirely.
    The device is assumed to exist; the callers check that first.
    """
    device = dr.async_get(hass).async_get(device_id)
    for entry_id in device.config_entries if device else ():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        # runtime_data is unset until the entry has been set up once.
        entry_data: WattpilotRuntimeData | None = getattr(entry, "runtime_data", None)
        if entry_data is not None:
            return entry_data
    return None


async def async_GetDataStoreFromDeviceID(hass: HomeAssistant, device_id: str) -> Any:
    """Async: return the data store for a specific device_id."""
    try:
        _LOGGER.debug("%s - async_GetDataStoreFromDeviceID: receiving device: %s", DOMAIN, device_id)
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(device_id)
        if device is None:
            _LOGGER.error("%s - async_GetDataStoreFromDeviceID: unknown device: %s", DOMAIN, device_id)
            return None

        _LOGGER.debug("%s - async_GetDataStoreFromDeviceID: get charger data store for config entry", DOMAIN)
        entry_data = _runtime_data_for_device(hass, device_id)
        if entry_data is None:
            _LOGGER.error(
                "%s - async_GetDataStoreFromDeviceID: Unable to receive data store for device: %s", DOMAIN, device_id
            )
            return None

        _LOGGER.debug("%s - async_GetDataStoreFromDeviceID: return data_entry", DOMAIN)
        return entry_data
    except Exception as e:
        _LOGGER.exception(
            "%s - async_GetDataStoreFromDeviceID: Could not get data store %s: %s (%s.%s)",
            DOMAIN,
            device_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False


async def async_GetChargerFromDeviceID(hass: HomeAssistant, device_id: str) -> Any:
    """Async: return the charger object for a specific device_id."""
    try:
        _LOGGER.debug("%s - async_GetChargerFromDeviceID: receiving device: %s", DOMAIN, device_id)
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(device_id)
        if device is None:
            _LOGGER.error("%s - async_GetChargerFromDeviceID: unknown device: %s", DOMAIN, device_id)
            return None

        _LOGGER.debug("%s - async_GetChargerFromDeviceID: get charger object and data store for config entry", DOMAIN)
        entry_data = _runtime_data_for_device(hass, device_id)
        charger = entry_data.charger if entry_data is not None else None
        if charger is None:
            _LOGGER.error(
                "%s - async_GetChargerFromDeviceID: Unable to identify charger object for device: %s", DOMAIN, device_id
            )
            return None

        _LOGGER.debug("%s - async_GetChargerFromDeviceID: return charger object", DOMAIN)
        return charger
    except Exception as e:
        _LOGGER.exception(
            "%s - async_GetChargerFromDeviceID: Could not get charger %s: %s (%s.%s)",
            DOMAIN,
            device_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False


async def async_ConnectCharger(
    entry_or_device_id: str, data: Mapping[str, Any], charger: Wattpilot | None = None
) -> Wattpilot | Literal[False]:
    """Async: connect charger and handle connection errors.

    Builds a wattpilot_api ``Wattpilot`` client (unless reconnecting an existing
    one) and awaits ``connect()``, which internally waits for authentication and
    property initialisation and raises on failure. Returns the connected charger,
    or ``False`` on a connection error. ``AuthenticationError`` is re-raised so
    callers (config-entry setup) can trigger a reauthentication flow.
    """
    try:
        con = data.get(CONF_CONNECTION, CONF_LOCAL)
        timeout = data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        if charger is None and con == CONF_CLOUD:
            id = data.get(CONF_SERIAL, None)
            _LOGGER.debug(
                "%s - async_ConnectCharger: Connecting %s charger by serial: %s", entry_or_device_id, CONF_CLOUD, id
            )
            charger = Wattpilot(
                host=id,
                password=data.get(CONF_PASSWORD, None),
                serial=id,
                cloud=True,
                connect_timeout=timeout,
                init_timeout=timeout,
            )
        elif charger is None:
            id = data.get(CONF_IP_ADDRESS, None)
            _LOGGER.debug(
                "%s - async_ConnectCharger: Connecting %s charger by ip: %s", entry_or_device_id, CONF_LOCAL, id
            )
            charger = Wattpilot(
                host=id,
                password=data.get(CONF_PASSWORD, None),
                serial=id,
                connect_timeout=timeout,
                init_timeout=timeout,
            )
        else:
            _LOGGER.debug("%s - async_ConnectCharger: Reconnect existing charger: %s", entry_or_device_id, charger.name)
        await charger.connect()
    except AuthenticationError as e:
        # The interpolated args are the entry id and the library's error string,
        # never the password itself.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- logs the entry id and error string, never the password  # noqa: E501
        _LOGGER.error(
            "%s - async_ConnectCharger: Authentication failed - check charger password: %s", entry_or_device_id, str(e)
        )
        raise
    except WattpilotError as e:
        _LOGGER.error(
            "%s - async_ConnectCharger: Connecting charger failed: %s (%s.%s)",
            entry_or_device_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False
    except Exception as e:
        _LOGGER.exception(
            "%s - async_ConnectCharger: Connecting charger failed: %s (%s.%s)",
            entry_or_device_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False

    _LOGGER.debug("%s - async_ConnectCharger: Charger connected: %s", entry_or_device_id, charger.name)
    return charger


async def async_PreloadApiDefinition(hass: HomeAssistant, entry_or_device_id: str, charger: Wattpilot) -> bool:
    """Async: read the API definition off the event loop and hand it to a charger.

    The read happens in an executor, at most once per Home Assistant run (see
    ``_load_api_definition``); filling the client's own cache with the result means
    its first write no longer reads ``wattpilot.yaml`` from disk on the event loop.
    Workaround for an upstream lazy load; it can go once the client loads its
    definition without blocking the caller.

    Args:
        hass: The Home Assistant instance.
        entry_or_device_id: The config entry id, for log correlation.
        charger: The connected charger whose cache is to be filled.

    Returns:
        ``True`` when the cache was filled, ``False`` when it was left to the client.
    """
    try:
        # Two entries setting up at once may both find the cache empty and parse the
        # file twice. That is wasted work, not a race: the result is the same either way.
        definition = await hass.async_add_executor_job(_load_api_definition)
        if definition is None:
            _LOGGER.debug("%s - async_PreloadApiDefinition: No API definition to hand over", entry_or_device_id)
            return False
        # The cache is private to the client, and deliberately written to anyway:
        # there is no public way to seed it. The attribute is checked rather than
        # assumed, so a rename costs only the pre-warm - writes still work, and the
        # client still loads what it needs, just lazily again.
        if not hasattr(charger, "_api_def_cache"):
            _LOGGER.debug(
                "%s - async_PreloadApiDefinition: Client caches its API definition elsewhere", entry_or_device_id
            )
            return False
        charger._api_def_cache = definition
        _LOGGER.debug("%s - async_PreloadApiDefinition: API definition handed to the charger", entry_or_device_id)
        return True
    except Exception as e:
        _LOGGER.exception(
            "%s - async_PreloadApiDefinition: Preloading the API definition failed: %s (%s.%s)",
            entry_or_device_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return False


async def async_DisconnectCharger(entry_or_device_id: str, charger: Wattpilot | Literal[False]) -> None:
    """Async: disconnect charger and handle connection errors."""
    if charger is False:
        return None
    try:
        _LOGGER.debug("%s - async_DisconnectCharger: disconnect charger: %s", entry_or_device_id, charger)
        await charger.disconnect()
        return None
    except Exception as e:
        _LOGGER.exception(
            "%s - async_DisconnectCharger: Disconnect charger failed: %s (%s.%s)",
            entry_or_device_id,
            str(e),
            e.__class__.__module__,
            type(e).__name__,
        )
        return None
