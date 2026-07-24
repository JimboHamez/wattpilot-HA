"""Unit tests for the pure helper logic in ``utils.py``.

Focuses on the two behaviours worth pinning down without a live charger:
value type-coercion in ``async_SetChargerProp`` and safe reads in
``GetChargerProp``. The module is imported lazily because importing it pulls in
Home Assistant and the vendored ``wattpilot`` library (which needs
``websocket-client``); if those are absent the whole module is skipped.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PARAMS, CONF_PASSWORD
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from pytest_homeassistant_custom_component.common import MockConfigEntry

from wattpilot_api.exceptions import AuthenticationError, WattpilotError

from custom_components.wattpilot.const import (
    CONF_CHARGER,
    CONF_CLOUD,
    CONF_CONNECTION,
    CONF_DBG_PROPS,
    CONF_LOCAL,
    CONF_SERIAL,
    DOMAIN,
    EVENT_PROPS_ID,
)

# Importing utils triggers Home Assistant imports and the dynamic load of the
# vendored wattpilot library (needs websocket-client). Skip the whole module
# cleanly if any of that is unavailable.
try:
    from custom_components.wattpilot import utils
except ImportError as exc:
    pytest.skip(f"integration import unavailable: {exc}", allow_module_level=True)


# --- async_SetChargerProp: type coercion -------------------------------------


async def test_numeric_string_coerced_to_int(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, "amp", "16") is True
    assert mock_charger.sent[-1] == ("amp", 16)
    assert isinstance(mock_charger.sent[-1][1], int)


async def test_boolean_like_string_coerced_to_bool(mock_charger):
    await utils.async_SetChargerProp(mock_charger, "cae", "true")
    assert mock_charger.sent[-1] == ("cae", True)
    assert isinstance(mock_charger.sent[-1][1], bool)


async def test_native_bool_coerced_to_bool(mock_charger):
    await utils.async_SetChargerProp(mock_charger, "cae", False)
    assert mock_charger.sent[-1] == ("cae", False)
    assert isinstance(mock_charger.sent[-1][1], bool)


async def test_force_type_str_keeps_numeric_as_string(mock_charger):
    await utils.async_SetChargerProp(mock_charger, "amp", 16, force_type="str")
    assert mock_charger.sent[-1] == ("amp", "16")
    assert isinstance(mock_charger.sent[-1][1], str)


async def test_force_type_float(mock_charger):
    await utils.async_SetChargerProp(mock_charger, "fte", "1.5", force_type="float")
    assert mock_charger.sent[-1] == ("fte", 1.5)
    assert isinstance(mock_charger.sent[-1][1], float)


async def test_namespace_value_sent_as_dict(mock_charger):
    mock_charger.all_properties["cll"] = SimpleNamespace(requestedCurrent=16)
    await utils.async_SetChargerProp(mock_charger, "cll", SimpleNamespace(requestedCurrent=16))
    assert mock_charger.sent[-1] == ("cll", {"requestedCurrent": 16})


# --- async_SetChargerProp: guard rails ---------------------------------------


async def test_unknown_property_without_force_is_rejected(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, "does_not_exist", 1) is False
    assert mock_charger.sent == []


async def test_unknown_property_with_force_is_written(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, "does_not_exist", 1, force=True) is True
    assert mock_charger.sent[-1] == ("does_not_exist", 1)


async def test_none_value_is_rejected(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, "amp", None) is False
    assert mock_charger.sent == []


# --- GetChargerProp: safe reads ----------------------------------------------


def test_get_existing_property(mock_charger):
    assert utils.GetChargerProp(mock_charger, "amp") == 6


def test_get_missing_property_returns_default(mock_charger):
    assert utils.GetChargerProp(mock_charger, "nope", default="fallback") == "fallback"


def test_get_none_property_returns_default(make_charger):
    charger = make_charger(props={"x": None})
    assert utils.GetChargerProp(charger, "x", default=42) == 42


def test_get_prop_on_object_without_allprops_returns_default():
    assert utils.GetChargerProp(object(), "amp", default="d") == "d"


# --- debug helpers -----------------------------------------------------------


async def test_async_programming_debug_lists_attributes(caplog):
    """The object dump logs public attributes at debug level."""
    obj = SimpleNamespace(visible="yes")
    with caplog.at_level(logging.DEBUG, logger="custom_components.wattpilot.utils"):
        await utils.async_ProgrammingDebug(obj)
    assert any("visible = yes" in r.getMessage() for r in caplog.records)


def test_programming_debug_lists_attributes(caplog):
    """The synchronous dump behaves like the async one."""
    with caplog.at_level(logging.DEBUG, logger="custom_components.wattpilot.utils"):
        utils.ProgrammingDebug(SimpleNamespace(visible="yes"))
    assert any("visible = yes" in r.getMessage() for r in caplog.records)


def test_programming_debug_survives_a_broken_object(caplog):
    """An attribute that raises on read is logged, not propagated."""

    class _Exploding:
        @property
        def boom(self):
            raise RuntimeError("no")

    with caplog.at_level(logging.DEBUG, logger="custom_components.wattpilot.utils"):
        utils.ProgrammingDebug(_Exploding())
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_property_debug_skips_noisy_properties(caplog):
    """Properties on the exclusion list are not logged when debugging is on."""
    with caplog.at_level(logging.WARNING, logger="custom_components.wattpilot.utils"):
        await utils.async_PropertyDebug("nrg", "1", True)
    assert caplog.records == []


async def test_property_debug_logs_other_properties(caplog):
    """Any property not excluded is logged while debugging is on."""
    with caplog.at_level(logging.WARNING, logger="custom_components.wattpilot.utils"):
        await utils.async_PropertyDebug("amp", "16", True)
    assert any("amp => 16" in r.getMessage() for r in caplog.records)


async def test_property_debug_honours_an_explicit_property_list(caplog):
    """An explicit watch list logs only the properties it names."""
    with caplog.at_level(logging.WARNING, logger="custom_components.wattpilot.utils"):
        await utils.async_PropertyDebug("nrg", "1", ["nrg"])
        await utils.async_PropertyDebug("amp", "16", ["nrg"])
    messages = [r.getMessage() for r in caplog.records]
    assert any("nrg => 1" in m for m in messages)
    assert not any("amp" in m for m in messages)


# --- async_PropertyUpdateHandler ---------------------------------------------


def _entry_with_runtime_data(hass, dbg=False):
    """Return a config entry with the runtime data the handler expects."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_FRIENDLY_NAME: "WB"})
    entry.add_to_hass(hass)
    entry.runtime_data = {CONF_PARAMS: {CONF_FRIENDLY_NAME: "WB"}, CONF_DBG_PROPS: dbg}
    return entry


async def test_property_update_is_dispatched_to_subscribers(hass):
    """A property update reaches whoever subscribed to that property's signal."""
    entry = _entry_with_runtime_data(hass)
    seen = []
    async_dispatcher_connect(hass, utils.property_update_signal(entry.entry_id, "amp"), seen.append)

    await utils.async_PropertyUpdateHandler(hass, entry, "amp", "16")
    await hass.async_block_till_done()

    assert seen == ["16"]


async def test_event_properties_are_fired_on_the_bus(hass):
    """Properties in EVENT_PROPS are re-broadcast as Home Assistant events."""
    entry = _entry_with_runtime_data(hass)
    events = []
    hass.bus.async_listen(EVENT_PROPS_ID, events.append)

    await utils.async_PropertyUpdateHandler(hass, entry, "ftt", "123")
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {"charger_id": "WB", "entry_id": entry.entry_id, "property": "ftt", "value": "123"}


async def test_non_event_properties_are_not_fired(hass):
    """Ordinary properties do not raise a Home Assistant event."""
    entry = _entry_with_runtime_data(hass)
    events = []
    hass.bus.async_listen(EVENT_PROPS_ID, events.append)

    await utils.async_PropertyUpdateHandler(hass, entry, "amp", "16")
    await hass.async_block_till_done()

    assert events == []


async def test_property_update_triggers_debug_logging(hass, caplog):
    """With debugging enabled the handler also runs the property debug log."""
    entry = _entry_with_runtime_data(hass, dbg=True)

    with caplog.at_level(logging.WARNING, logger="custom_components.wattpilot.utils"):
        await utils.async_PropertyUpdateHandler(hass, entry, "amp", "16")
        await hass.async_block_till_done()

    assert any("amp => 16" in r.getMessage() for r in caplog.records)


async def test_property_update_without_runtime_data_is_logged(hass, caplog):
    """A handler call before runtime data exists is logged, not raised."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.utils"):
        assert await utils.async_PropertyUpdateHandler(hass, entry, "amp", "16") is None

    assert any("async_PropertyUpdateHandler" in r.getMessage() for r in caplog.records)


# --- async_GetChargerProp / GetChargerProp -----------------------------------


async def test_async_get_existing_property(mock_charger):
    assert await utils.async_GetChargerProp(mock_charger, "amp") == 6


async def test_async_get_missing_property_returns_default(mock_charger):
    assert await utils.async_GetChargerProp(mock_charger, "nope", default="fallback") == "fallback"


async def test_async_get_none_property_returns_default(make_charger):
    charger = make_charger(props={"x": None})
    assert await utils.async_GetChargerProp(charger, "x", default=42) == 42


async def test_async_get_prop_on_object_without_allprops_returns_default():
    assert await utils.async_GetChargerProp(object(), "amp", default="d") == "d"


async def test_async_get_prop_survives_a_broken_property_dict():
    """A property dict that raises on access falls back to the default."""

    class _BrokenCharger:
        @property
        def all_properties(self):
            raise RuntimeError("boom")

    # hasattr() swallows the raise, so the identifier check reports it missing.
    assert await utils.async_GetChargerProp(_BrokenCharger(), "amp", default="d") == "d"
    assert utils.GetChargerProp(_BrokenCharger(), "amp", default="d") == "d"


# --- async_SetChargerProp: remaining guard rails ------------------------------


async def test_set_prop_without_identifier_is_rejected(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, None, 1) is False
    assert mock_charger.sent == []


async def test_set_prop_on_object_without_allprops_is_rejected():
    assert await utils.async_SetChargerProp(object(), "amp", 1) is False


async def test_set_prop_falls_back_to_string(mock_charger):
    """A value that is neither bool nor numeric is sent as a string."""
    mock_charger.all_properties["ct"] = "car"
    await utils.async_SetChargerProp(mock_charger, "ct", "Second Car")
    assert mock_charger.sent[-1] == ("ct", "Second Car")


async def test_set_prop_reports_a_failing_write(make_charger, caplog):
    """A charger that rejects the write is logged and reported as failure."""
    charger = make_charger(props={"amp": 6})
    charger.set_property = AsyncMock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.utils"):
        assert await utils.async_SetChargerProp(charger, "amp", 16) is False

    assert any("Could not set property amp" in r.getMessage() for r in caplog.records)


# --- device-id lookups --------------------------------------------------------


def _registered_device(hass, charger):
    """Register a device for an entry holding the given charger."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    entry.runtime_data = {CONF_CHARGER: charger, CONF_PARAMS: {}}
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "SN")}, name="WB"
    )
    return entry, device


async def test_get_charger_and_data_store_from_device_id(hass, mock_charger):
    """Both lookups resolve a registered device to its entry runtime data."""
    entry, device = _registered_device(hass, mock_charger)

    assert await utils.async_GetChargerFromDeviceID(hass, device.id) is mock_charger
    assert await utils.async_GetDataStoreFromDeviceID(hass, device.id) is entry.runtime_data


async def test_lookups_report_an_unknown_device(hass):
    """An unknown device id resolves to nothing, without raising."""
    assert await utils.async_GetChargerFromDeviceID(hass, "nope") is None
    assert await utils.async_GetDataStoreFromDeviceID(hass, "nope") is None


async def test_lookups_report_an_entry_without_runtime_data(hass):
    """A device whose entry carries no runtime data resolves to nothing."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "SN2")}, name="WB2"
    )

    assert await utils.async_GetChargerFromDeviceID(hass, device.id) is None
    assert await utils.async_GetDataStoreFromDeviceID(hass, device.id) is None


# --- connect / disconnect -----------------------------------------------------


def _client_mock():
    """Return a stand-in for the wattpilot_api client class and its instance."""
    instance = MagicMock()
    instance.configure_mock(name="WB")
    instance.connect = AsyncMock()
    instance.disconnect = AsyncMock()
    return MagicMock(return_value=instance), instance


async def test_connect_local_charger_uses_the_ip_address():
    """A local entry builds the client from its IP address."""
    client, instance = _client_mock()
    data = {CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "1.2.3.4", CONF_PASSWORD: "p"}

    with patch("custom_components.wattpilot.utils.Wattpilot", client):
        assert await utils.async_ConnectCharger("entry-1", data) is instance

    assert client.call_args.kwargs["host"] == "1.2.3.4"
    assert "cloud" not in client.call_args.kwargs
    instance.connect.assert_awaited_once()


async def test_connect_cloud_charger_uses_the_serial():
    """A cloud entry builds the client from its serial, in cloud mode."""
    client, instance = _client_mock()
    data = {CONF_CONNECTION: CONF_CLOUD, CONF_SERIAL: "SN", CONF_PASSWORD: "p"}

    with patch("custom_components.wattpilot.utils.Wattpilot", client):
        assert await utils.async_ConnectCharger("entry-1", data) is instance

    assert client.call_args.kwargs["host"] == "SN"
    assert client.call_args.kwargs["serial"] == "SN"
    assert client.call_args.kwargs["cloud"] is True


async def test_reconnect_reuses_the_existing_charger():
    """Reconnecting an existing charger does not build a second client."""
    client, instance = _client_mock()

    with patch("custom_components.wattpilot.utils.Wattpilot", client):
        assert await utils.async_ConnectCharger("entry-1", {}, instance) is instance

    client.assert_not_called()
    instance.connect.assert_awaited_once()


async def test_connect_reraises_authentication_errors():
    """A wrong password is re-raised so setup can start a reauth flow."""
    client, instance = _client_mock()
    instance.connect = AsyncMock(side_effect=AuthenticationError("bad password"))

    with patch("custom_components.wattpilot.utils.Wattpilot", client), pytest.raises(AuthenticationError):
        await utils.async_ConnectCharger("entry-1", {CONF_IP_ADDRESS: "1.2.3.4"})


@pytest.mark.parametrize("error", [WattpilotError("offline"), RuntimeError("boom")])
async def test_connect_failures_return_false(error):
    """Connection failures are reported as False, not raised."""
    client, instance = _client_mock()
    instance.connect = AsyncMock(side_effect=error)

    with patch("custom_components.wattpilot.utils.Wattpilot", client):
        assert await utils.async_ConnectCharger("entry-1", {CONF_IP_ADDRESS: "1.2.3.4"}) is False


async def test_disconnect_closes_the_session(mock_charger):
    """Disconnecting a live charger closes its session."""
    assert await utils.async_DisconnectCharger("entry-1", mock_charger) is None
    assert mock_charger.connected is False


async def test_disconnect_ignores_a_charger_that_never_connected():
    """The False sentinel used before a successful connect is a no-op."""
    assert await utils.async_DisconnectCharger("entry-1", False) is None


async def test_disconnect_failure_is_logged(make_charger, caplog):
    """A failing disconnect is logged and degraded, not raised."""
    charger = make_charger(props={})
    charger.disconnect = AsyncMock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.utils"):
        assert await utils.async_DisconnectCharger("entry-1", charger) is None

    assert any("Disconnect charger failed" in r.getMessage() for r in caplog.records)


async def test_async_programming_debug_survives_a_broken_object(caplog):
    """An attribute that raises on read is logged, not propagated."""

    class _Exploding:
        @property
        def boom(self):
            raise RuntimeError("no")

    with caplog.at_level(logging.DEBUG, logger="custom_components.wattpilot.utils"):
        await utils.async_ProgrammingDebug(_Exploding())
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_lookups_degrade_when_the_device_registry_fails(hass):
    """A registry failure is logged and reported, not raised."""
    with patch("custom_components.wattpilot.utils.dr.async_get", side_effect=RuntimeError("boom")):
        assert await utils.async_GetChargerFromDeviceID(hass, "any") is False
        assert await utils.async_GetDataStoreFromDeviceID(hass, "any") is False
