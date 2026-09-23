"""Live check of the 0.12.0 unique-id migration against a real charger.

``tests/test_migration.py`` covers the migration with a mock charger. This module
runs it with **nothing mocked**: a real Home Assistant instance with an entity
registry laid out the way 0.11.0 left it (an entry keyed by the charger's IP,
entities keyed by its friendly name) sets the entry up against the physical
charger, and the test checks that the entry and its entities end up keyed by the
serial the charger reports, keeping their entity ids and showing live values.
A second test runs the local config flow and checks a new entry is keyed by serial.

Not collected by a normal ``pytest`` run (``pytest.ini`` only matches ``test_*.py``).
Run it explicitly, with charger details in the gitignored ``.wp_test.json``:

    .venv/bin/python -m pytest tests/live_migration.py -v

READ-ONLY as far as the charger is concerned: it connects, reads properties and
disconnects. Everything it migrates lives in the test's throwaway registry.
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT, STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import CONF_CONNECTION, CONF_LOCAL, CONF_SERIAL, DOMAIN

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_FILE = os.path.join(REPO_ROOT, ".wp_test.json")

# (entity domain, catalog uid) pairs the Flex reports; registered under the old prefix.
LEGACY_ENTITIES = [("sensor", "tma"), ("sensor", "eto"), ("switch", "fup"), ("number", "amp"), ("select", "lmo")]

pytestmark = pytest.mark.skipif(not os.path.exists(SECRETS_FILE), reason="no .wp_test.json with charger details")


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled):
    """Let the test harness reach the charger (pytest-socket pins the allowlist to localhost)."""
    import pytest_socket

    pytest_socket.socket_allow_hosts(["127.0.0.1", "::1", _config()["ip"]], allow_unix_socket=True)
    yield
    pytest_socket.disable_socket()


def _config() -> dict:
    """Return the charger's connection details from the gitignored secrets file."""
    with open(SECRETS_FILE, encoding="utf-8") as handle:
        cfg = json.load(handle)
    if cfg.get("connection", CONF_LOCAL) != CONF_LOCAL:
        pytest.skip("the migration re-keys local entries; .wp_test.json describes a cloud charger")
    return cfg


async def test_an_ip_keyed_install_migrates_onto_the_serial(hass):
    """A 0.11.0-style entry and registry move onto the charger's serial, entity ids intact."""
    cfg = _config()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Garage",
        unique_id=cfg["ip"],
        data={
            CONF_CONNECTION: CONF_LOCAL,
            CONF_IP_ADDRESS: cfg["ip"],
            CONF_PASSWORD: cfg["password"],
            CONF_FRIENDLY_NAME: "Garage",
            CONF_TIMEOUT: 30,
        },
    )
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    legacy = {
        (domain, uid): ent_reg.async_get_or_create(
            domain, DOMAIN, f"Garage-{uid}", config_entry=entry, suggested_object_id=f"garage_{uid}"
        ).entity_id
        for domain, uid in LEGACY_ENTITIES
    }

    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        serial = entry.unique_id
        assert serial == str(cfg["serial"]), "the entry was not re-keyed by the serial the charger reports"
        assert entry.data[CONF_SERIAL] == serial
        assert entry.data[CONF_IP_ADDRESS] == cfg["ip"]

        registered = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        unique_ids = {e.unique_id for e in registered}
        assert not any(uid.startswith("Garage-") for uid in unique_ids), "entities left under the old prefix"
        assert not any(e.entity_id.endswith("_2") for e in registered), "an entity was created twice"
        for (domain, uid), entity_id in legacy.items():
            assert ent_reg.async_get_entity_id(domain, DOMAIN, f"{serial}-{uid}") == entity_id, entity_id
            state = hass.states.get(entity_id)
            assert state is not None and state.state != STATE_UNAVAILABLE, f"{entity_id}: {state}"
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_the_local_flow_keys_a_new_entry_by_serial(hass):
    """Adding the charger by IP creates an entry keyed by its serial, with the serial stored."""
    cfg = _config()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CONNECTION: CONF_LOCAL})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_FRIENDLY_NAME: "Garage",
            CONF_IP_ADDRESS: cfg["ip"],
            CONF_PASSWORD: cfg["password"],
            CONF_TIMEOUT: 30,
        },
    )
    await hass.async_block_till_done()

    try:
        assert result["type"] == FlowResultType.CREATE_ENTRY
        entry = result["result"]
        assert entry.unique_id == str(cfg["serial"])
        assert entry.data[CONF_SERIAL] == str(cfg["serial"])
    finally:
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
