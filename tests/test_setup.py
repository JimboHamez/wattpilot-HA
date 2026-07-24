"""Integration test for setup, runtime-data storage, and the property push path.

Drives ``async_setup_entry`` with a mock charger (so no real WebSocket is
opened), then simulates the library firing a property-change callback and
asserts the update reaches the entity through the dispatcher.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.icon import async_get_icons
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import CONF_CHARGER, CONF_CONNECTION, CONF_LOCAL, DOMAIN


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


async def test_setup_stores_runtime_data_and_push_updates_entity(hass, make_charger):
    """Setup stores runtime data; a charger property push updates the entity."""
    charger = make_charger(
        props={"tma": 5.0, "car": 1, "amp": 6, "nrg": [0] * 16, "frc": 0, "typ": "model", "var": 11, "sse": "SN"},
        serial="SN",
        name="WB",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "1.2.3.4", "friendly_name": "WB", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)

    with patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # runtime-data: per-entry state lives on the config entry, not hass.data.
    assert entry.runtime_data[CONF_CHARGER] is charger
    assert DOMAIN not in hass.data

    # The charger-temperature entity was created (keyed by its unchanged unique_id).
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, "WB-tma")
    assert entity_id is not None
    assert hass.states.get(entity_id) is not None

    # entity-event-setup: simulate the library firing a property change; it must
    # reach the entity through the dispatcher and update its state.
    assert charger._property_callbacks, "setup did not register a property callback"
    await charger._property_callbacks[0]("tma", 42.0)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "42.0"

    # icon-translations: entities carry no _attr_icon; Home Assistant loads the
    # icons from icons.json instead and the frontend resolves them per
    # translation key, so the loading is what has to hold here.
    served = (await async_get_icons(hass, "entity", integrations=[DOMAIN]))[DOMAIN]
    assert served["sensor"]["tma"] == {"default": "mdi:thermometer"}
    assert hass.states.get(entity_id).attributes.get("icon") is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
