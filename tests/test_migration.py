"""Tests for moving config entries and entities onto serial-based unique ids.

Driven through ``hass.config_entries.async_setup`` with a pre-populated entity
registry, so the migration runs exactly where it does on upgrade: after the
charger connects, before the platforms add their entities.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import CONF_CLOUD, CONF_CONNECTION, CONF_LOCAL, CONF_SERIAL, DOMAIN

PROPS = {"tma": 5.0, "fup": True, "car": 1, "typ": "model", "var": 11, "sse": "SN"}
LOCAL_DATA = {CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "1.2.3.4", "friendly_name": "WB", CONF_PASSWORD: "p"}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


def _ip_keyed_entry(hass, **data):
    """Return a manually added local entry as created before 0.12.0: keyed by its IP."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="1.2.3.4", data={**LOCAL_DATA, **data})
    entry.add_to_hass(hass)
    return entry


def _register(hass, entry, domain, unique_id, object_id):
    """Register an entity the way an earlier version left it in the registry."""
    return er.async_get(hass).async_get_or_create(
        domain, DOMAIN, unique_id, config_entry=entry, suggested_object_id=object_id
    )


async def _setup(hass, entry, charger):
    """Set the entry up against the given charger."""
    with patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _unique_ids(hass, entry):
    """Return the unique ids of the entry's registered entities."""
    return {e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}


async def test_upgrade_keeps_entity_ids_and_rekeys_by_serial(hass, make_charger):
    """Existing entities move to serial-based ids and keep their entity ids; the entry follows."""
    entry = _ip_keyed_entry(hass)
    temperature = _register(hass, entry, "sensor", "WB-tma", "garage_temperature")
    surplus = _register(hass, entry, "switch", "WB-fup", "garage_surplus")

    await _setup(hass, entry, make_charger(props=dict(PROPS), serial="SN", name="WB"))

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "SN-tma") == temperature.entity_id
    assert ent_reg.async_get_entity_id("switch", DOMAIN, "SN-fup") == surplus.entity_id
    # No entity was created twice, and none is left under the old prefix.
    assert not any(uid.startswith("WB-") for uid in _unique_ids(hass, entry))
    assert hass.states.get(temperature.entity_id).state == "5.0"
    assert entry.unique_id == "SN"
    assert entry.data[CONF_SERIAL] == "SN"
    assert entry.data[CONF_IP_ADDRESS] == "1.2.3.4"


async def test_migration_is_idempotent(hass, make_charger):
    """Setting the entry up again changes nothing further."""
    entry = _ip_keyed_entry(hass)
    _register(hass, entry, "sensor", "WB-tma", "garage_temperature")
    charger = make_charger(props=dict(PROPS), serial="SN", name="WB")
    await _setup(hass, entry, charger)
    before = _unique_ids(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await _setup(hass, entry, charger)

    assert _unique_ids(hass, entry) == before
    assert entry.unique_id == "SN"


async def test_an_entity_whose_new_id_is_taken_is_left_alone(hass, make_charger, caplog):
    """A collision is skipped with a warning instead of failing setup."""
    entry = _ip_keyed_entry(hass)
    old = _register(hass, entry, "sensor", "WB-tma", "garage_temperature")
    new = _register(hass, entry, "sensor", "SN-tma", "garage_temperature_2")

    with caplog.at_level(logging.WARNING, logger="custom_components.wattpilot.migration"):
        await _setup(hass, entry, make_charger(props=dict(PROPS), serial="SN", name="WB"))

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "WB-tma") == old.entity_id
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "SN-tma") == new.entity_id
    assert any("its new unique id is taken" in r.getMessage() for r in caplog.records)


async def test_entities_orphaned_by_an_earlier_rename_are_left_alone(hass, make_charger):
    """Only the entry's current prefix is migrated; an older prefix has no live counterpart."""
    entry = _ip_keyed_entry(hass)
    _register(hass, entry, "sensor", "Old name-tma", "old_temperature")

    await _setup(hass, entry, make_charger(props=dict(PROPS), serial="SN", name="WB"))

    assert "Old name-tma" in _unique_ids(hass, entry)
    assert "SN-tma" in _unique_ids(hass, entry)


async def test_two_chargers_with_the_default_name_both_get_their_entities(hass, make_charger):
    """Two chargers left at the default name no longer collide on their entity ids."""
    first = MockConfigEntry(domain=DOMAIN, unique_id="A1", data={**LOCAL_DATA, "friendly_name": "Wattpilot"})
    second = MockConfigEntry(domain=DOMAIN, unique_id="B2", data={**LOCAL_DATA, "friendly_name": "Wattpilot"})
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    chargers = {
        first.entry_id: make_charger(props={**PROPS, "sse": "A1"}, serial="A1", name="Garage"),
        second.entry_id: make_charger(props={**PROPS, "sse": "B2"}, serial="B2", name="Carport"),
    }

    # Setting up the integration sets up both entries; each connects to its own charger.
    async def _connect(entry_id, _data):
        return chargers[entry_id]

    with patch("custom_components.wattpilot.async_ConnectCharger", new=_connect):
        assert await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()

    assert "A1-tma" in _unique_ids(hass, first)
    assert "B2-tma" in _unique_ids(hass, second)


async def test_a_serial_keyed_entry_only_has_its_entities_moved(hass, make_charger):
    """A cloud or discovered entry already carries the serial; only its entities change."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SN",
        data={CONF_CONNECTION: CONF_CLOUD, CONF_SERIAL: "SN", "friendly_name": "WB", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)
    _register(hass, entry, "sensor", "WB-tma", "garage_temperature")

    await _setup(hass, entry, make_charger(props=dict(PROPS), serial="SN", name="WB"))

    assert "SN-tma" in _unique_ids(hass, entry)
    assert entry.unique_id == "SN"
    assert entry.data[CONF_CONNECTION] == CONF_CLOUD


async def test_a_charger_without_a_serial_is_not_migrated(hass, make_charger):
    """Without a serial there is nothing to key by; the legacy ids stay in use."""
    entry = _ip_keyed_entry(hass)
    _register(hass, entry, "sensor", "WB-tma", "garage_temperature")
    props = {key: value for key, value in PROPS.items() if key != "sse"}

    await _setup(hass, entry, make_charger(props=props, serial="", name="WB"))

    assert "WB-tma" in _unique_ids(hass, entry)
    assert entry.unique_id == "1.2.3.4"
    assert CONF_SERIAL not in entry.data


async def test_the_same_charger_configured_twice_keeps_the_entry_key(hass, make_charger, caplog):
    """If another entry already holds the serial, the entry is not re-keyed and the duplicate is reported."""
    MockConfigEntry(domain=DOMAIN, unique_id="SN", title="Discovered", data=dict(LOCAL_DATA)).add_to_hass(hass)
    entry = _ip_keyed_entry(hass)

    with caplog.at_level(logging.WARNING, logger="custom_components.wattpilot.migration"):
        await _setup(hass, entry, make_charger(props=dict(PROPS), serial="SN", name="WB"))

    assert entry.unique_id == "1.2.3.4"
    assert CONF_SERIAL not in entry.data
    assert any("is also configured as Discovered" in r.getMessage() for r in caplog.records)


async def test_a_name_equal_to_the_serial_needs_no_migration(hass, make_charger):
    """An entry already named after its serial has ids that are already right."""
    entry = _ip_keyed_entry(hass, friendly_name="SN")
    registered = _register(hass, entry, "sensor", "SN-tma", "garage_temperature")

    await _setup(hass, entry, make_charger(props=dict(PROPS), serial="SN", name="WB"))

    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "SN-tma") == registered.entity_id


async def test_a_failing_migration_does_not_block_setup(hass, make_charger, caplog):
    """The migration is logged and skipped on failure; the charger is still set up."""
    entry = _ip_keyed_entry(hass)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot"),
        patch("custom_components.wattpilot.async_migrate_unique_ids", side_effect=RuntimeError("boom")),
    ):
        await _setup(hass, entry, make_charger(props=dict(PROPS), serial="SN", name="WB"))

    assert any("Migrating unique ids failed" in r.getMessage() for r in caplog.records)
    assert "SN-tma" in _unique_ids(hass, entry)
