"""Config-flow tests for the Fronius Wattpilot integration.

These exercise the UI setup flow end-to-end using the Home Assistant test
harness (the `hass` fixture from pytest-homeassistant-custom-component).
`async_setup_entry` is patched so completing a flow does not try to open a real
WebSocket to a charger — we only assert on the flow's behaviour.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant import config_entries
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_IP_ADDRESS,
    CONF_PASSWORD,
)
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import (
    CONF_CLOUD,
    CONF_CONNECTION,
    CONF_LOCAL,
    CONF_SERIAL,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


async def _start_flow(hass):
    """Begin the user flow and return the first (connection) form result."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return result


async def test_user_flow_starts_with_connection_form(hass):
    """The flow opens on the connection-type selection form."""
    result = await _start_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == CONF_CONNECTION


async def test_local_flow_creates_entry(hass):
    """A local connection produces an entry keyed by the charger IP."""
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION: CONF_LOCAL}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == CONF_LOCAL

    with patch("custom_components.wattpilot.async_setup_entry", return_value=True):
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
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION: CONF_CLOUD}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == CONF_CLOUD

    with patch("custom_components.wattpilot.async_setup_entry", return_value=True):
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
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION: CONF_LOCAL}
    )
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
