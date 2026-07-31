"""Config-flow tests for the Fronius Wattpilot integration.

These exercise the UI setup flow end-to-end using the Home Assistant test
harness (the `hass` fixture from pytest-homeassistant-custom-component).
`async_setup_entry` is patched so completing a flow does not try to open a real
WebSocket to a charger — we only assert on the flow's behaviour.
"""

from __future__ import annotations

import logging
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from wattpilot_api.exceptions import AuthenticationError

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant import config_entries
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_IP_ADDRESS,
    CONF_PASSWORD,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.config_flow import ConfigFlowHandler
from custom_components.wattpilot.const import (
    CONF_CLOUD,
    CONF_CONNECTION,
    CONF_LOCAL,
    CONF_SERIAL,
    DOMAIN,
)

from .conftest import MockCharger


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


async def _start_flow(hass):
    """Begin the user flow and return the first (connection) form result."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return result


async def test_user_flow_starts_with_connection_form(hass):
    """The flow opens on the connection-type selection form."""
    result = await _start_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == CONF_CONNECTION


async def test_local_flow_creates_entry(hass):
    """A local connection produces an entry keyed by the charger IP."""
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CONNECTION: CONF_LOCAL})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == CONF_LOCAL

    with (
        patch("custom_components.wattpilot.config_flow.async_ConnectCharger", new=AsyncMock(return_value=object())),
        patch("custom_components.wattpilot.config_flow.async_DisconnectCharger", new=AsyncMock()),
        patch("custom_components.wattpilot.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_FRIENDLY_NAME: "Garage",
                CONF_IP_ADDRESS: "192.168.1.50",
                CONF_PASSWORD: "secret",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage"
    assert result["data"][CONF_CONNECTION] == CONF_LOCAL
    assert result["data"][CONF_IP_ADDRESS] == "192.168.1.50"
    assert result["result"].unique_id == "192.168.1.50"


async def test_cloud_flow_creates_entry(hass):
    """A cloud connection produces an entry keyed by the charger serial."""
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CONNECTION: CONF_CLOUD})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == CONF_CLOUD

    with (
        patch("custom_components.wattpilot.config_flow.async_ConnectCharger", new=AsyncMock(return_value=object())),
        patch("custom_components.wattpilot.config_flow.async_DisconnectCharger", new=AsyncMock()),
        patch("custom_components.wattpilot.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_FRIENDLY_NAME: "Cloud WP",
                CONF_SERIAL: "123456",
                CONF_PASSWORD: "secret",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONNECTION] == CONF_CLOUD
    assert result["result"].unique_id == "123456"


async def test_duplicate_local_charger_aborts(hass):
    """Re-adding a charger with the same IP aborts (unique-config-entry)."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.50",
        data={CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "192.168.1.50"},
    ).add_to_hass(hass)

    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CONNECTION: CONF_LOCAL})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_FRIENDLY_NAME: "Garage",
            CONF_IP_ADDRESS: "192.168.1.50",
            CONF_PASSWORD: "secret",
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_local_flow_invalid_auth_shows_error(hass):
    """A wrong password is validated before the entry is created (test-before-configure)."""
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CONNECTION: CONF_LOCAL})

    with patch(
        "custom_components.wattpilot.config_flow.async_ConnectCharger",
        new=AsyncMock(side_effect=AuthenticationError("wrong password")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FRIENDLY_NAME: "Garage", CONF_IP_ADDRESS: "192.168.1.51", CONF_PASSWORD: "wrong"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == CONF_LOCAL
    assert result["errors"] == {"base": "invalid_auth"}


def _discovery(properties: dict | None = None, addresses: list[str] | None = None) -> ZeroconfServiceInfo:
    """Build a ZeroconfServiceInfo matching a real Wattpilot Flex advertisement."""
    ips = [ip_address(a) for a in addresses] if addresses else [ip_address("192.168.0.32")]
    props = (
        {
            "serial": "91111999",
            "friendly_name": "Wattpilot_91111999",
            "manufacturer": "fronius",
            "devicefamily": "wattpilot",
            "devicetype": "wattpilot_flex",
        }
        if properties is None
        else properties
    )
    return ZeroconfServiceInfo(
        # Home Assistant reports the first non-link-local address as the host,
        # which is an IPv6 one whenever no IPv4 record was announced first.
        ip_address=ips[0],
        ip_addresses=ips,
        port=80,
        hostname="Wattpilot-91111999.local.",
        type="_http._tcp.local.",
        name="Wattpilot-91111999._http._tcp.local.",
        properties=props,
    )


async def test_zeroconf_discovery_creates_entry(hass):
    """A discovered charger prompts for the password, then creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery()
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"

    with patch("custom_components.wattpilot.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "secret"})
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_IP_ADDRESS] == "192.168.0.32"
    assert result["data"][CONF_SERIAL] == "91111999"
    assert result["data"][CONF_PASSWORD] == "secret"
    assert result["result"].unique_id == "91111999"


async def test_zeroconf_already_configured_updates_ip_and_aborts(hass):
    """A discovered charger with a known serial aborts and refreshes its IP."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="91111999",
        data={CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "192.168.0.9"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery()
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.0.32"


async def test_zeroconf_prefers_the_ipv4_address(hass):
    """An announcement led by an IPv6 address is still stored as its IPv4 one."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_discovery(addresses=["2001:db8::b9ff:7f16:4163:9afc", "192.168.0.32"]),
    )
    assert result["type"] == FlowResultType.FORM

    with patch("custom_components.wattpilot.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "secret"})
        await hass.async_block_till_done()

    assert result["data"][CONF_IP_ADDRESS] == "192.168.0.32"


async def test_zeroconf_without_ipv4_aborts_and_keeps_the_stored_address(hass):
    """An IPv6-only announcement is ignored instead of overwriting a working IP."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="91111999",
        data={CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "192.168.0.32"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_discovery(addresses=["2001:db8::b9ff:7f16:4163:9afc"]),
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_ipv4"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.0.32"


async def test_zeroconf_without_serial_aborts(hass):
    """A discovery advertisement lacking a serial number aborts cleanly."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_discovery(properties={"devicefamily": "wattpilot"}),
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_serial"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.50",
        data={CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "192.168.1.50", CONF_PASSWORD: "old"},
    )


async def test_reauth_updates_password(hass):
    """A successful reauth validates the new password and updates the entry."""
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with (
        patch("custom_components.wattpilot.config_flow.async_ConnectCharger", new=AsyncMock(return_value=object())),
        patch("custom_components.wattpilot.config_flow.async_DisconnectCharger", new=AsyncMock()),
        patch("custom_components.wattpilot.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "newpass"})
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "newpass"


async def test_reauth_invalid_password_shows_error(hass):
    """A wrong password during reauth re-shows the form with an error."""
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)

    with patch(
        "custom_components.wattpilot.config_flow.async_ConnectCharger",
        new=AsyncMock(side_effect=AuthenticationError("wrong password")),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "wrong"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "old"


def _reachable(serial: str = "91111999"):
    """Patch the flow so connecting succeeds and reports the given charger serial."""
    return (
        patch(
            "custom_components.wattpilot.config_flow.async_ConnectCharger",
            new=AsyncMock(return_value=MockCharger({"sse": serial})),
        ),
        patch("custom_components.wattpilot.config_flow.async_DisconnectCharger", new=AsyncMock()),
        patch("custom_components.wattpilot.async_setup_entry", return_value=True),
    )


async def test_reconfigure_updates_the_local_connection(hass):
    """A moved charger is repointed at its new address (reconfiguration-flow)."""
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    connect, disconnect, setup = _reachable()
    with connect, disconnect, setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_FRIENDLY_NAME: "Garage",
                CONF_IP_ADDRESS: "192.168.1.99",
                CONF_PASSWORD: "secret",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.1.99"
    assert entry.data[CONF_PASSWORD] == "secret"
    # The entry was keyed by its IP address, so its identity moves with it.
    assert entry.unique_id == "192.168.1.99"
    assert entry.title == "Garage"


async def test_reconfigure_keeps_a_serial_keyed_identity(hass):
    """A discovered entry keeps its serial unique id when its address changes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="91111999",
        data={
            CONF_CONNECTION: CONF_LOCAL,
            CONF_IP_ADDRESS: "192.168.0.32",
            CONF_SERIAL: "91111999",
            CONF_PASSWORD: "old",
        },
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    connect, disconnect, setup = _reachable()
    with connect, disconnect, setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FRIENDLY_NAME: "Garage", CONF_IP_ADDRESS: "192.168.0.44", CONF_PASSWORD: "old"},
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.0.44"
    assert entry.unique_id == "91111999"


async def test_reconfigure_rejects_a_different_charger(hass):
    """An address that answers with another charger's serial is refused."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="91111999",
        data={
            CONF_CONNECTION: CONF_LOCAL,
            CONF_IP_ADDRESS: "192.168.0.32",
            CONF_SERIAL: "91111999",
            CONF_PASSWORD: "old",
        },
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    connect, disconnect, setup = _reachable(serial="22222222")
    with connect, disconnect, setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FRIENDLY_NAME: "Garage", CONF_IP_ADDRESS: "192.168.0.77", CONF_PASSWORD: "old"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "wrong_charger"}
    assert entry.data[CONF_IP_ADDRESS] == "192.168.0.32", "the entry must not be repointed"


async def test_reconfigure_cloud_entry_rejects_a_changed_serial(hass):
    """A cloud entry is its serial, so changing it aborts rather than repointing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        data={CONF_CONNECTION: CONF_CLOUD, CONF_SERIAL: "123456", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_FRIENDLY_NAME: "Cloud WP", CONF_SERIAL: "999999", CONF_PASSWORD: "old"},
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "wrong_charger"
    assert entry.data[CONF_SERIAL] == "123456"


async def test_reconfigure_cloud_entry_updates_the_password(hass):
    """A cloud entry keeping its serial is reconfigured normally."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        data={CONF_CONNECTION: CONF_CLOUD, CONF_SERIAL: "123456", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    connect, disconnect, setup = _reachable(serial="123456")
    with connect, disconnect, setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FRIENDLY_NAME: "Cloud WP", CONF_SERIAL: "123456", CONF_PASSWORD: "new"},
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_reconfigure_onto_an_already_configured_address_aborts(hass):
    """Moving an entry onto another configured charger's address is refused."""
    entry = _entry()
    entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.99",
        data={CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "192.168.1.99"},
    ).add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    connect, disconnect, setup = _reachable()
    with connect, disconnect, setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FRIENDLY_NAME: "Garage", CONF_IP_ADDRESS: "192.168.1.99", CONF_PASSWORD: "secret"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.1.50"


async def test_reconfigure_unreachable_charger_shows_error(hass):
    """A charger that cannot be reached re-shows the form and changes nothing."""
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    with patch("custom_components.wattpilot.config_flow.async_ConnectCharger", new=AsyncMock(return_value=False)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FRIENDLY_NAME: "Garage", CONF_IP_ADDRESS: "192.168.1.99", CONF_PASSWORD: "secret"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_IP_ADDRESS] == "192.168.1.50"


async def test_reconfigure_unexpected_failure_aborts(hass):
    """An unexpected error inside the step is logged and aborts the flow."""
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    with patch("custom_components.wattpilot.config_flow.async_ConnectCharger", side_effect=RuntimeError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FRIENDLY_NAME: "Garage", CONF_IP_ADDRESS: "192.168.1.99", CONF_PASSWORD: "secret"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "exception"


# --- flows against a fully set-up entry ---------------------------------------
#
# The tests above drive the flows against an entry that was never set up, which is
# enough for the flow's own logic but misses anything that reacts to the entry being
# updated. An `add_update_listener` that rewrote entry.data from entry.options used to
# live here, and it emptied the entry whenever reauth or reconfigure finished. These
# two run the flows against an entry that really went through async_setup_entry.


async def _setup_live_entry(hass, charger):
    """Add an entry and run the real async_setup_entry against a mock charger."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.50",
        data={
            CONF_CONNECTION: CONF_LOCAL,
            CONF_IP_ADDRESS: "192.168.1.50",
            CONF_PASSWORD: "old",
            CONF_FRIENDLY_NAME: "WB",
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_reconfigure_of_a_loaded_entry_keeps_its_data(hass):
    """Reconfiguring a set-up entry stores the new details and loses nothing else."""
    charger = MockCharger({"sse": "91111999", "amp": 6, "car": 1, "typ": "model", "var": 11})
    entry = await _setup_live_entry(hass, charger)

    result = await entry.start_reconfigure_flow(hass)
    with (
        patch("custom_components.wattpilot.config_flow.async_ConnectCharger", new=AsyncMock(return_value=charger)),
        patch("custom_components.wattpilot.config_flow.async_DisconnectCharger", new=AsyncMock()),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FRIENDLY_NAME: "WB", CONF_IP_ADDRESS: "192.168.1.99", CONF_PASSWORD: "new"},
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.1.99"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.data[CONF_CONNECTION] == CONF_LOCAL, "the rest of the entry must survive"


async def test_reauth_of_a_loaded_entry_keeps_its_data(hass):
    """Reauthenticating a set-up entry updates the password and loses nothing else."""
    charger = MockCharger({"sse": "91111999", "amp": 6, "car": 1, "typ": "model", "var": 11})
    entry = await _setup_live_entry(hass, charger)

    result = await entry.start_reauth_flow(hass)
    with (
        patch("custom_components.wattpilot.config_flow.async_ConnectCharger", new=AsyncMock(return_value=charger)),
        patch("custom_components.wattpilot.config_flow.async_DisconnectCharger", new=AsyncMock()),
        patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "newpass"})
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "newpass"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.1.50", "the rest of the entry must survive"


# --- config flow failure branches ---------------------------------------------


async def test_user_step_reports_a_failure(hass, caplog):
    """A failure in the first user step aborts the flow."""
    flow = ConfigFlowHandler()
    flow.hass = hass

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"),
        patch.object(flow, "async_step_connection", side_effect=RuntimeError("boom")),
    ):
        result = await flow.async_step_user()

    assert result["reason"] == "exception"


async def test_connection_step_reports_a_failure(hass, caplog):
    """A failure while branching on the connection type aborts the flow."""
    flow = ConfigFlowHandler()
    flow.hass = hass

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"),
        patch.object(flow, "async_step_local", side_effect=RuntimeError("boom")),
    ):
        result = await flow.async_step_connection({CONF_CONNECTION: CONF_LOCAL})

    assert result["reason"] == "exception"


@pytest.mark.parametrize(
    ("step", "user_input"),
    [
        ("async_step_local", {CONF_IP_ADDRESS: "1.2.3.4", CONF_PASSWORD: "p"}),
        ("async_step_cloud", {CONF_SERIAL: "SN", CONF_PASSWORD: "p"}),
    ],
)
async def test_connection_steps_report_a_failure(hass, caplog, step, user_input):
    """A failure while validating a connection aborts the flow."""
    flow = ConfigFlowHandler()
    flow.hass = hass

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"),
        patch.object(flow, "_async_test_connection", side_effect=RuntimeError("boom")),
    ):
        result = await getattr(flow, step)(dict(user_input))

    assert result["reason"] == "exception"


async def test_zeroconf_step_reports_a_failure(hass, caplog):
    """A malformed discovery payload aborts rather than raising."""
    flow = ConfigFlowHandler()
    flow.hass = hass

    # Reaches the step (it logs the host) but has no discovery properties.
    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"):
        result = await flow.async_step_zeroconf(SimpleNamespace(host="1.2.3.4"))

    assert result["reason"] == "exception"


async def test_zeroconf_confirm_reports_a_failure(hass, caplog):
    """A failure while confirming a discovery aborts the flow."""
    flow = ConfigFlowHandler()
    flow.hass = hass
    flow.data = None  # breaks the name lookup

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"):
        result = await flow.async_step_zeroconf_confirm()

    assert result["reason"] == "exception"


async def test_reauth_reports_a_charger_it_cannot_reach(hass):
    """A charger that does not answer during reauth shows a connect error."""
    entry = _entry()
    entry.add_to_hass(hass)
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    flow = next(f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN)

    with patch("custom_components.wattpilot.config_flow.async_ConnectCharger", new=AsyncMock(return_value=False)):
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_PASSWORD: "new"})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
