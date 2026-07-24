"""Tests for config entry setup, teardown and the failure paths in between.

``async_setup_entry`` is driven directly (rather than through
``hass.config_entries.async_setup``) so each step can be made to fail on its
own: platform forwarding, the property callback registration and the
connection monitor all abort setup, while a charger that cannot be reached
must raise ``ConfigEntryNotReady`` so Home Assistant retries later.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wattpilot_api.exceptions import AuthenticationError

from custom_components.wattpilot import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
    options_update_listener,
)
from custom_components.wattpilot.const import (
    CONF_CHARGER,
    CONF_CONNECTION,
    CONF_LOCAL,
    DOMAIN,
    FUNC_CONNECTION_MONITOR,
    FUNC_OPTION_UPDATES,
    FUNC_PROPERTY_UPDATES_CALLBACK,
)

ENTRY_DATA = {
    CONF_CONNECTION: CONF_LOCAL,
    CONF_IP_ADDRESS: "1.2.3.4",
    CONF_PASSWORD: "p",
    "friendly_name": "WB",
}
CHARGER_PROPS = {"amp": 6, "car": 1, "nrg": [0] * 16, "frc": 0, "sse": "SN", "tma": 5.0, "typ": "model", "var": 11}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


def _entry(hass):
    """Return a config entry ready to be set up."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    return entry


def _no_platforms(hass):
    """Patch platform forwarding out of the way for a direct setup call."""
    return patch.multiple(
        hass.config_entries,
        async_forward_entry_setups=AsyncMock(),
        async_forward_entry_unload=AsyncMock(return_value=True),
    )


# --- service registration -----------------------------------------------------


async def test_async_setup_registers_the_services(hass):
    """The integration's services exist without any config entry loaded."""
    assert await async_setup(hass, {}) is True

    for service in (
        "disconnect_charger",
        "reconnect_charger",
        "set_goe_cloud",
        "set_debug_properties",
        "set_next_trip",
    ):
        assert hass.services.has_service(DOMAIN, service), f"{service} was not registered"


async def test_async_setup_reports_a_registration_failure(hass, caplog):
    """A failure while registering services is logged and reported."""
    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
        patch("custom_components.wattpilot.async_registerService", side_effect=RuntimeError("boom")),
    ):
        assert await async_setup(hass, {}) is False

    assert any("register services failed" in r.getMessage() for r in caplog.records)


# --- entry setup --------------------------------------------------------------


async def test_setup_entry_stores_runtime_data_and_starts_the_monitor(hass, make_charger):
    """A successful setup wires up the callback, listener and monitor."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")
    entry = _entry(hass)

    with (
        _no_platforms(hass),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert entry.runtime_data[CONF_CHARGER] is charger
    assert callable(entry.runtime_data[FUNC_OPTION_UPDATES])
    assert callable(entry.runtime_data[FUNC_PROPERTY_UPDATES_CALLBACK])
    assert callable(entry.runtime_data[FUNC_CONNECTION_MONITOR])


async def test_setup_entry_retries_when_the_charger_is_unreachable(hass):
    """A charger that cannot be reached leaves the entry to be retried."""
    entry = _entry(hass)

    with (
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=False)),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)


async def test_setup_entry_starts_reauth_on_a_bad_password(hass):
    """A rejected password asks the user to re-enter it."""
    entry = _entry(hass)

    with (
        patch(
            "custom_components.wattpilot.async_ConnectCharger",
            new=AsyncMock(side_effect=AuthenticationError("nope")),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, entry)


async def test_setup_entry_retries_after_an_unexpected_connect_error(hass, caplog):
    """Any other connect failure is logged and retried later."""
    entry = _entry(hass)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(side_effect=RuntimeError("boom"))),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)

    assert any("Connecting charger failed" in r.getMessage() for r in caplog.records)


async def test_setup_entry_survives_an_unknown_integration_version(hass, make_charger, caplog):
    """Setup continues when the integration version cannot be determined."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")
    entry = _entry(hass)

    with (
        caplog.at_level(logging.WARNING, logger="custom_components.wattpilot"),
        _no_platforms(hass),
        patch("custom_components.wattpilot.async_get_integration", side_effect=RuntimeError("boom")),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert any("Unable to determine" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("async_forward_entry_setups", "Setup trigger failed"),
        ("on_property_change", "Could not register properties updater handler"),
        ("connection_monitor", "Could not start charger connection monitor"),
    ],
)
async def test_setup_entry_aborts_when_a_step_fails(hass, make_charger, caplog, target, message):
    """Each wiring step aborts setup and is reported."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")
    entry = _entry(hass)

    patches = [
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
        patch.object(hass.config_entries, "async_forward_entry_unload", AsyncMock(return_value=True)),
    ]
    if target == "async_forward_entry_setups":
        patches.append(
            patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock(side_effect=RuntimeError("boom")))
        )
    else:
        patches.append(patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()))
    if target == "on_property_change":
        charger.on_property_change = MagicMock(side_effect=RuntimeError("boom"))
    if target == "connection_monitor":
        patches.append(patch("custom_components.wattpilot.ChargerConnectionMonitor", side_effect=RuntimeError("boom")))

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"):
        for p in patches:
            p.start()
        try:
            assert await async_setup_entry(hass, entry) is False
        finally:
            for p in patches:
                p.stop()

    assert any(message in r.getMessage() for r in caplog.records)


# --- options -------------------------------------------------------------------


async def test_options_update_listener_reloads_the_entry(hass):
    """Changing options copies them onto the entry and reloads it."""
    entry = _entry(hass)
    hass.config_entries.async_update_entry(entry, options={**ENTRY_DATA, CONF_IP_ADDRESS: "5.6.7.8"})

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await options_update_listener(hass, entry)

    assert entry.data[CONF_IP_ADDRESS] == "5.6.7.8"
    reload.assert_awaited_once_with(entry.entry_id)


async def test_options_update_listener_reports_a_failure(hass, caplog):
    """A failing reload is logged rather than raised at Home Assistant."""
    entry = _entry(hass)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
        patch.object(hass.config_entries, "async_reload", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        await options_update_listener(hass, entry)

    assert any("update options failed" in r.getMessage() for r in caplog.records)


# --- entry unload -------------------------------------------------------------


async def test_unload_entry_releases_everything(hass, make_charger):
    """Unloading stops the monitor, unsubscribes and disconnects the charger."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")
    entry = _entry(hass)

    with (
        _no_platforms(hass),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        assert await async_setup_entry(hass, entry) is True
        assert await async_unload_entry(hass, entry) is True

    assert charger.connected is False
    assert charger._property_callbacks == []


async def test_unload_entry_reports_a_failing_disconnect(hass, make_charger, caplog):
    """A charger that will not disconnect is reported, and unload completes."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")
    entry = _entry(hass)

    with (
        _no_platforms(hass),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        assert await async_setup_entry(hass, entry) is True
        with (
            caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
            patch(
                "custom_components.wattpilot.async_DisconnectCharger",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            assert await async_unload_entry(hass, entry) is True

    assert any("could not disconnect charger" in r.getMessage() for r in caplog.records)


async def test_unload_entry_reports_a_missing_runtime_data(hass, caplog):
    """Unloading an entry that was never set up is reported, not raised."""
    entry = _entry(hass)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
        patch.object(hass.config_entries, "async_forward_entry_unload", AsyncMock(return_value=True)),
    ):
        assert await async_unload_entry(hass, entry) is False

    assert any("Unload device failed" in r.getMessage() for r in caplog.records)


async def test_setup_entry_reports_a_failing_runtime_data_store(hass, make_charger, caplog):
    """A runtime data store that cannot be written aborts setup."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")

    class _BrokenEntry:
        """A config entry whose runtime data cannot be written."""

        entry_id = "broken"
        data = ENTRY_DATA

        @property
        def runtime_data(self):
            return {}

        @runtime_data.setter
        def runtime_data(self, value):
            raise RuntimeError("boom")

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        assert await async_setup_entry(hass, _BrokenEntry()) is False

    assert any("Creating data store failed" in r.getMessage() for r in caplog.records)


async def test_setup_entry_reports_a_failing_option_listener(hass, make_charger, caplog):
    """A listener that cannot be registered aborts setup."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")
    entry = _entry(hass)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
        _no_platforms(hass),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
        patch.object(entry, "add_update_listener", side_effect=RuntimeError("boom")),
    ):
        assert await async_setup_entry(hass, entry) is False

    assert any("Register option updates listener failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("key", "message"),
    [
        (FUNC_CONNECTION_MONITOR, "failed to stop charger connection monitor"),
        (FUNC_PROPERTY_UPDATES_CALLBACK, "failed to remove registered event handlers"),
    ],
)
async def test_unload_entry_reports_a_failing_teardown_step(hass, make_charger, caplog, key, message):
    """A teardown step that fails is reported, and unload continues."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")
    entry = _entry(hass)

    with (
        _no_platforms(hass),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        assert await async_setup_entry(hass, entry) is True

        def _explode():
            raise RuntimeError("boom")

        entry.runtime_data[key] = _explode
        with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"):
            assert await async_unload_entry(hass, entry) is True

    assert any(message in r.getMessage() for r in caplog.records)


async def test_unload_entry_reports_a_platform_that_will_not_unload(hass, make_charger, caplog):
    """A platform that refuses to unload is reported and unload fails."""
    charger = make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")
    entry = _entry(hass)

    with (
        _no_platforms(hass),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        assert await async_setup_entry(hass, entry) is True

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
        patch.object(hass.config_entries, "async_forward_entry_unload", AsyncMock(return_value=False)),
        patch("custom_components.wattpilot.asyncio.gather", new=AsyncMock(return_value=[])),
    ):
        assert await async_unload_entry(hass, entry) is False

    assert any("failed to unload" in r.getMessage() for r in caplog.records)
