"""Live check of the next-trip and charging-schedule actions against a real charger.

Runs ``wattpilot.set_next_trip`` and ``wattpilot.set_charging_schedule`` through a
real Home Assistant instance, as the action UI would call them, against the
physical charger. Each test writes back the value the charger already holds and
reads it back, so the charger's settings do not change.

``set_next_trip`` is run with the host process in Australia/Sydney: before 0.12.1
the value it sent depended on the host's time zone (06:00 became -14400 there),
which a UTC test run could never show.

Not collected by a normal ``pytest`` run (``pytest.ini`` only matches ``test_*.py``).
Run it explicitly, with charger details in the gitignored ``.wp_test.json``:

    .venv/bin/python -m pytest tests/live_actions.py -v

WRITES to the charger, but only its current values: ``ftt`` and ``sch_week``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import CONF_CONNECTION, CONF_LOCAL, DOMAIN
from custom_components.wattpilot.schedule import decode_schedule
from custom_components.wattpilot.utils import GetChargerProp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_FILE = os.path.join(REPO_ROOT, ".wp_test.json")

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


@pytest.fixture
def _sydney_host(monkeypatch):
    """Run the host process in Australia/Sydney, restoring the time zone afterwards."""
    monkeypatch.setenv("TZ", "Australia/Sydney")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _config() -> dict:
    """Return the charger's connection details from the gitignored secrets file."""
    with open(SECRETS_FILE, encoding="utf-8") as handle:
        cfg = json.load(handle)
    if cfg.get("connection", CONF_LOCAL) != CONF_LOCAL:
        pytest.skip("these checks connect locally; .wp_test.json describes a cloud charger")
    return cfg


@pytest.fixture
async def charger_device(hass):
    """Set an entry up against the real charger; yield the charger and its device id."""
    cfg = _config()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(cfg["serial"]),
        data={
            CONF_CONNECTION: CONF_LOCAL,
            CONF_IP_ADDRESS: cfg["ip"],
            CONF_PASSWORD: cfg["password"],
            CONF_FRIENDLY_NAME: "Live",
            CONF_TIMEOUT: 30,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    charger = entry.runtime_data.charger
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, charger.serial)})
    assert device is not None
    yield charger, device.id
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _settle() -> None:
    """Give the charger a moment to apply the write and push the value back."""
    await asyncio.sleep(3)


async def test_set_next_trip_round_trips_on_a_sydney_host(hass, charger_device, _sydney_host):
    """The next-trip time the picker sends is stored as seconds since the charger's midnight."""
    charger, device_id = charger_device
    current = int(GetChargerProp(charger, "ftt"))
    picked = f"{current // 3600:02d}:{current % 3600 // 60:02d}:{current % 60:02d}"

    await hass.services.async_call(
        DOMAIN, "set_next_trip", {"device_id": device_id, "trigger_time": picked}, blocking=True
    )
    await _settle()

    assert GetChargerProp(charger, "ftt") == current, f"picked {picked}, charger holds {GetChargerProp(charger, 'ftt')}"


async def test_set_charging_schedule_round_trips_the_ui_payload(hass, charger_device):
    """The weekday schedule, sent in the action UI's shape, is taken by the charger unchanged."""
    charger, device_id = charger_device
    before = decode_schedule(GetChargerProp(charger, "sch_week"))
    assert before is not None, "the charger reports no weekday schedule"

    await hass.services.async_call(
        DOMAIN,
        "set_charging_schedule",
        {
            "device_id": device_id,
            "day_type": "weekdays",
            "limit_charging_times": before["limit_charging_times"],
            "pv_surplus_outside_times": before["pv_surplus_outside_times"],
            # The object selector hands over a list of {begin, end} dicts of HH:MM strings.
            "ranges": [{"begin": r["begin"], "end": r["end"]} for r in before["ranges"]],
        },
        blocking=True,
    )
    await _settle()

    assert decode_schedule(GetChargerProp(charger, "sch_week")) == before
