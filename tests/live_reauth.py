"""Live end-to-end check of the reauthentication flow against a real charger.

The 0.8.0 fix (an update listener that overwrote ``entry.data`` with ``entry.options``)
was Home Assistant-side, so ``tests/test_config_flow.py`` covers it with a mock charger.
This module runs the flows with **nothing mocked**: a real Home Assistant instance sets a
config entry up against the physical charger, then reconfigures and reauthenticates it.

Only the *reconfigure* test detects the 0.8.0 bug. Home Assistant fires update listeners
solely when an entry actually changed, so re-entering the same password never triggered
the faulty listener - the reauth tests here cover the charger-facing half instead.

Not collected by a normal ``pytest`` run (``pytest.ini`` only matches ``test_*.py``).
Run it explicitly, with charger details in the gitignored ``.wp_test.json``:

    .venv/bin/python -m pytest tests/live_reauth.py -v

READ-ONLY as far as the charger is concerned: it connects, reads properties and
disconnects. It does send one deliberately wrong password to check that branch.
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import (
    CONF_CLOUD,
    CONF_CONNECTION,
    CONF_SERIAL,
    DOMAIN,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_FILE = os.path.join(REPO_ROOT, ".wp_test.json")

pytestmark = pytest.mark.skipif(not os.path.exists(SECRETS_FILE), reason="no .wp_test.json with charger details")


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled):
    """Let the test harness talk to the real charger.

    ``socket_enabled`` re-enables sockets, but pytest-socket still pins the host
    allowlist to localhost, so the charger's address has to be added explicitly.
    """
    import pytest_socket

    with open(SECRETS_FILE, encoding="utf-8") as handle:
        cfg = json.load(handle)
    hosts = ["127.0.0.1", "::1"]
    if cfg.get("ip"):
        hosts.append(cfg["ip"])
    if cfg.get("connection") == CONF_CLOUD:
        # The cloud path resolves go-e hostnames, so DNS and any address is needed.
        pytest_socket.enable_socket()
        yield
        return
    pytest_socket.socket_allow_hosts(hosts, allow_unix_socket=True)
    yield
    pytest_socket.disable_socket()


def _config() -> dict:
    """Return the charger's connection details from the gitignored secrets file."""
    with open(SECRETS_FILE, encoding="utf-8") as handle:
        cfg = json.load(handle)
    con = cfg.get("connection", "local")
    data = {
        CONF_CONNECTION: con,
        CONF_FRIENDLY_NAME: "live-reauth",
        CONF_PASSWORD: cfg["password"],
        CONF_TIMEOUT: int(cfg.get("timeout", 30)),
    }
    if con == CONF_CLOUD:
        data[CONF_SERIAL] = cfg["serial"]
    else:
        data[CONF_IP_ADDRESS] = cfg["ip"]
    return data


async def _loaded_entry(hass) -> MockConfigEntry:
    """Set an entry up against the real charger and return it."""
    data = _config()
    unique = str(data.get(CONF_SERIAL) or data.get(CONF_IP_ADDRESS))
    entry = MockConfigEntry(domain=DOMAIN, unique_id=unique, data=data, title="live-reauth")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id), "the charger could not be set up"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_reconfigure_against_the_real_charger_keeps_the_entry(hass):
    """A real reconfiguration stores the change and leaves the rest of the entry intact.

    This is the test that detects the 0.8.0 bug. It has to change something the entry
    did not already hold: Home Assistant only fires update listeners when the entry
    actually changed, so re-submitting identical values would never have triggered the
    faulty listener. The name and timeout are changed because neither touches the
    charger — only how Home Assistant labels and waits for it.
    """
    entry = await _loaded_entry(hass)
    before = dict(entry.data)

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    changed = {**before, CONF_FRIENDLY_NAME: "live-reauth-renamed", CONF_TIMEOUT: before[CONF_TIMEOUT] + 1}
    submitted = {k: v for k, v in changed.items() if k != CONF_CONNECTION}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], submitted)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    # The regression: before 0.8.0 an update listener emptied entry.data here.
    assert entry.data.get(CONF_FRIENDLY_NAME) == "live-reauth-renamed"
    assert entry.data.get(CONF_PASSWORD) == before[CONF_PASSWORD], f"entry data lost: {dict(entry.data)}"
    assert entry.data.get(CONF_CONNECTION) == before[CONF_CONNECTION], f"entry data lost: {dict(entry.data)}"
    assert entry.state is ConfigEntryState.LOADED, "the entry did not come back after the reload"
    assert hass.states.async_entity_ids(), "the entities did not come back after the reload"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_reauth_against_the_real_charger_keeps_the_entry(hass):
    """A real reauth updates the password and leaves the rest of the entry intact.

    Re-submitting the same password is not an entry change, so this does not exercise
    the 0.8.0 listener bug (the reconfigure test above does). What it does check is the
    charger-facing half: that the physical charger really re-authenticates and the
    entry reloads afterwards.
    """
    entry = await _loaded_entry(hass)
    before = dict(entry.data)
    assert hass.states.async_entity_ids(), "setting up the charger produced no entities"

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    # Re-enter the same (correct) password: the charger really re-authenticates.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: before[CONF_PASSWORD]})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    assert dict(entry.data) == before, f"entry data changed: {dict(entry.data)}"
    assert entry.state is ConfigEntryState.LOADED, "the entry did not come back after the reload"
    assert hass.states.async_entity_ids(), "the entities did not come back after the reload"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_reauth_with_a_wrong_password_changes_nothing(hass):
    """The real charger rejects a wrong password, and the entry is left alone."""
    entry = await _loaded_entry(hass)
    before = dict(entry.data)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "definitely-not-the-password"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert dict(entry.data) == before, f"entry data changed: {dict(entry.data)}"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
