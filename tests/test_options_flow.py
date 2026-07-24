"""Tests for the options flow and the config flow's failure branches.

The options flow re-runs the connection questions against an existing entry;
its answers land in ``entry.options`` and the entry is reloaded by
``options_update_listener``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant import config_entries
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.config_flow import ConfigFlowHandler, OptionsFlowHandler
from custom_components.wattpilot.const import CONF_CLOUD, CONF_CONNECTION, CONF_LOCAL, CONF_SERIAL, DOMAIN

LOCAL_DATA = {
    CONF_CONNECTION: CONF_LOCAL,
    CONF_FRIENDLY_NAME: "WB",
    CONF_IP_ADDRESS: "1.2.3.4",
    CONF_PASSWORD: "p",
    CONF_TIMEOUT: 15,
}
CLOUD_DATA = {
    CONF_CONNECTION: CONF_CLOUD,
    CONF_FRIENDLY_NAME: "WB",
    CONF_SERIAL: "SN",
    CONF_PASSWORD: "p",
    CONF_TIMEOUT: 15,
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


def _entry(hass, data=None, source=config_entries.SOURCE_USER):
    """Return a config entry the options flow can run against."""
    entry = MockConfigEntry(domain=DOMAIN, data=data or LOCAL_DATA, source=source)
    entry.add_to_hass(hass)
    return entry


async def test_options_flow_starts_with_the_connection_question(hass):
    """The options flow opens on the connection-type form."""
    entry = _entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "config_connection"


async def test_options_flow_updates_local_settings(hass):
    """Answering the local form stores the new settings on the entry."""
    entry = _entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_CONNECTION: CONF_LOCAL}
    )
    assert result["step_id"] == "config_local"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_FRIENDLY_NAME: "Wallbox",
            CONF_IP_ADDRESS: "5.6.7.8",
            CONF_PASSWORD: "new",
            CONF_TIMEOUT: 20,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_IP_ADDRESS] == "5.6.7.8"
    assert entry.options[CONF_CONNECTION] == CONF_LOCAL


async def test_options_flow_updates_cloud_settings(hass):
    """Answering the cloud form stores the new settings on the entry."""
    entry = _entry(hass, data=CLOUD_DATA)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_CONNECTION: CONF_CLOUD}
    )
    assert result["step_id"] == "config_cloud"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_FRIENDLY_NAME: "Wallbox",
            CONF_SERIAL: "SN2",
            CONF_PASSWORD: "new",
            CONF_TIMEOUT: 20,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SERIAL] == "SN2"
    assert entry.options[CONF_CONNECTION] == CONF_CLOUD


async def test_options_flow_rejects_an_unsupported_entry_source(hass):
    """Entries that were not added by hand cannot be reconfigured this way."""
    entry = _entry(hass, source=config_entries.SOURCE_ZEROCONF)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_options_flow_rejects_an_unknown_connection_type(hass):
    """A connection type that is neither local nor cloud aborts the flow."""
    entry = _entry(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_config_connection({CONF_CONNECTION: "carrier-pigeon"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.parametrize("step", ["async_step_config_local", "async_step_config_cloud"])
async def test_options_flow_reports_a_broken_schema(hass, caplog, step):
    """A schema that cannot be built aborts the step instead of raising."""
    entry = _entry(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    target = "async_get_OPTIONS_LOCAL_SCHEMA" if step.endswith("local") else "async_get_OPTIONS_CLOUD_SCHEMA"

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"),
        patch(f"custom_components.wattpilot.config_flow.{target}", side_effect=RuntimeError("boom")),
    ):
        result = await getattr(flow, step)()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "exception"


async def test_options_flow_reports_a_broken_connection_step(hass, caplog):
    """A failure in the connection step aborts rather than raising."""
    entry = _entry(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"),
        patch.object(flow, "async_step_config_local", side_effect=RuntimeError("boom")),
    ):
        result = await flow.async_step_config_connection({CONF_CONNECTION: CONF_LOCAL})

    assert result["reason"] == "exception"


async def test_options_flow_reports_a_broken_init_step(hass, caplog):
    """A failure while deciding the first step aborts the flow."""
    entry = _entry(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"),
        patch.object(flow, "async_step_config_connection", side_effect=RuntimeError("boom")),
    ):
        result = await flow.async_step_init()

    assert result["reason"] == "exception"


async def test_options_flow_reloads_an_entry_left_in_error(hass):
    """Options changed on a failed entry trigger the reload listener."""
    entry = _entry(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    flow.data = dict(LOCAL_DATA)

    entry.mock_state(hass, config_entries.ConfigEntryState.SETUP_ERROR)
    with patch("custom_components.wattpilot.config_flow.options_update_listener", new=AsyncMock()) as listener:
        result = await flow.async_step_final()

    listener.assert_awaited_once()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_reports_a_broken_final_step(hass, caplog):
    """A failure while saving the options aborts instead of raising."""
    entry = _entry(hass)
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    flow.data = None  # breaks the title lookup

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.config_flow"):
        result = await flow.async_step_final()

    assert result["reason"] == "exception"


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
    entry = _entry(hass)
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    flow = next(f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN)

    with patch("custom_components.wattpilot.config_flow.async_ConnectCharger", new=AsyncMock(return_value=False)):
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_PASSWORD: "new"})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
