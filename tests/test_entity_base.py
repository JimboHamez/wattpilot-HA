"""Tests for the shared entity base class.

``ChargerPlatformEntity`` decides which entities exist at all (firmware,
variant and connection gating), whether they are available, and how a raw
charger value becomes entity state for the three value sources. It is driven
here directly, with hand-written definitions rather than catalog entries, so
each branch can be reached in isolation.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("wattpilot_api", reason="integration import unavailable")
from homeassistant.const import CONF_PARAMS, STATE_UNKNOWN, EntityCategory

from custom_components.wattpilot.const import CONF_CLOUD, CONF_CONNECTION, CONF_LOCAL
from custom_components.wattpilot.entities import ChargerPlatformEntity

BASE_PROPS = {"amp": 6, "onv": "38.5", "sse": "SN", "typ": "model", "var": 11}


def _build(charger, entry_connection=CONF_LOCAL, **cfg):
    """Build a base entity from a hand-written definition."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {"friendly_name": "WB"}
    entry.runtime_data = {CONF_PARAMS: {CONF_CONNECTION: entry_connection}}
    definition = {"source": "property", "id": "amp"}
    definition.update(cfg)
    return ChargerPlatformEntity(hass, entry, definition, charger)


# --- construction gating ------------------------------------------------------


@pytest.mark.parametrize(
    ("test", "supported"),
    [
        (">=38.0", True),
        (">=39.0", False),
        ("<=38.5", True),
        ("<=38.0", False),
        ("==38.5", True),
        ("==40.0", False),
        (">38.0", True),
        (">38.5", False),
        ("<39.0", True),
        ("<38.5", False),
    ],
)
def test_firmware_gate_compares_against_the_charger_firmware(make_charger, test, supported):
    """Each comparison prefix is evaluated against the charger firmware."""
    charger = make_charger(props=dict(BASE_PROPS), firmware="38.5")
    entity = _build(charger, firmware=test)

    assert entity._fw_supported is supported
    assert entity._init_failed is not supported


def test_firmware_gate_rejects_an_unknown_operator(make_charger, caplog):
    """A test string with no comparison prefix disables the entity."""
    charger = make_charger(props=dict(BASE_PROPS), firmware="38.5")

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"):
        entity = _build(charger, firmware="38.5")

    assert entity._fw_supported is False
    assert any("Invalid firmware version test string" in r.getMessage() for r in caplog.records)


def test_firmware_gate_without_a_readable_firmware(make_charger, caplog):
    """A charger that reports no firmware fails the gate."""
    charger = make_charger(props={"amp": 6, "typ": "model", "var": 11})

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"):
        entity = _build(charger, firmware=">=38.0")

    assert entity._fw_supported is False
    assert any("Cannot identify Charger firmware" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(("variant", "supported"), [(11, True), (22, False)])
def test_variant_gate(make_charger, variant, supported):
    """An entity limited to one hardware variant is skipped on the other."""
    charger = make_charger(props=dict(BASE_PROPS))

    entity = _build(charger, variant=variant)

    assert entity._variant_supported is supported


@pytest.mark.parametrize(("connection", "supported"), [(CONF_LOCAL, True), (CONF_CLOUD, False)])
def test_connection_gate(make_charger, connection, supported):
    """An entity limited to one connection type is skipped on the other."""
    charger = make_charger(props=dict(BASE_PROPS))

    entity = _build(charger, connection=connection)

    assert entity._connection_supported is supported


def test_connection_gate_passes_without_runtime_data(make_charger):
    """Before runtime data exists the connection gate does not block setup."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {"friendly_name": "WB"}
    entry.runtime_data = None
    charger = make_charger(props=dict(BASE_PROPS))

    entity = ChargerPlatformEntity(hass, entry, {"source": "property", "id": "amp", "connection": CONF_CLOUD}, charger)

    assert entity._connection_supported is True


@pytest.mark.parametrize(
    ("source", "identifier"),
    [("property", "does_not_exist"), ("attribute", "does_not_exist"), ("namespacelist", "cards_0")],
)
def test_absent_values_skip_the_entity(make_charger, source, identifier):
    """A value the charger does not report means "skip", not "error"."""
    charger = make_charger(props=dict(BASE_PROPS))

    entity = _build(charger, source=source, id=identifier)

    assert entity._init_failed is True


def test_construction_failure_is_logged(make_charger, caplog):
    """An unexpected error during construction is logged, not raised."""
    charger = make_charger(props=dict(BASE_PROPS))

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"),
        patch("custom_components.wattpilot.entities.slugify", side_effect=RuntimeError("boom")),
    ):
        entity = _build(charger)

    assert any("__init__ failed" in r.getMessage() for r in caplog.records)
    assert entity._init_failed is False  # the failure happened after the gates passed


# --- entity metadata ----------------------------------------------------------


def test_translation_key_and_unique_id_come_from_the_definition(make_charger):
    """The uid drives both the translation key and the unique id."""
    charger = make_charger(props=dict(BASE_PROPS))

    entity = _build(charger, uid="Max Current")

    assert entity._attr_translation_key == "max_current"
    assert entity._attr_unique_id == "WB-Max Current"


def test_description_and_attributes(make_charger):
    """The description is exposed both as a property and a state attribute."""
    charger = make_charger(props=dict(BASE_PROPS))

    entity = _build(charger, description="Charging current")

    assert entity.description == "Charging current"
    assert entity.extra_state_attributes["description"] == "Charging current"


def test_entity_category(make_charger):
    """A configured category is returned as the Home Assistant enum."""
    charger = make_charger(props=dict(BASE_PROPS))

    assert _build(charger, entity_category="diagnostic").entity_category is EntityCategory.DIAGNOSTIC
    assert _build(charger).entity_category is None


@pytest.mark.parametrize(("enabled", "expected"), [(True, True), (False, False), ("false", False), (None, True)])
def test_entity_registry_enabled_default(make_charger, enabled, expected):
    """The 'enabled' flag accepts booleans and bool-like strings."""
    charger = make_charger(props=dict(BASE_PROPS))
    cfg = {} if enabled is None else {"enabled": enabled}

    assert _build(charger, **cfg).entity_registry_enabled_default is expected


def test_entity_registry_enabled_default_degrades_to_enabled(make_charger, caplog):
    """An unreadable 'enabled' flag leaves the entity enabled."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity._entity_cfg = MagicMock()
    entity._entity_cfg.get.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"):
        assert entity.entity_registry_enabled_default is True

    assert any("entity_registry_enabled_default failed" in r.getMessage() for r in caplog.records)


def test_device_info_describes_the_charger(make_charger):
    """The device registry entry is built from the charger's own metadata."""
    charger = make_charger(props=dict(BASE_PROPS), serial="SN", manufacturer="Fronius", firmware="38.5")

    info = _build(charger).device_info

    assert info["identifiers"] == {("wattpilot", "SN")}
    assert info["manufacturer"] == "Fronius"
    assert info["model"] == "model"
    assert info["sw_version"] == "38.5"
    assert info["hw_version"] == "11 KW"


# --- availability -------------------------------------------------------------


def test_available_when_everything_is_in_place(make_charger):
    """A fully supported entity on a connected charger is available."""
    assert _build(make_charger(props=dict(BASE_PROPS))).available is True


def test_unavailable_when_construction_failed(make_charger):
    """An entity that never finished construction is unavailable."""
    entity = _build(make_charger(props=dict(BASE_PROPS)), id="does_not_exist")

    assert entity.available is False


@pytest.mark.parametrize("gate", ["_fw_supported", "_variant_supported", "_connection_supported"])
def test_unavailable_when_a_gate_failed(make_charger, gate):
    """Each unsupported gate makes the entity unavailable."""
    entity = _build(make_charger(props=dict(BASE_PROPS)))
    setattr(entity, gate, False)

    assert entity.available is False


@pytest.mark.parametrize("flag", ["connected", "properties_initialized"])
def test_unavailable_while_the_charger_is_not_ready(make_charger, flag):
    """A disconnected or uninitialised charger makes entities unavailable."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    setattr(charger, flag, False)

    assert entity.available is False


def test_unavailable_when_the_value_disappears(make_charger):
    """A property that vanishes at runtime makes the entity unavailable."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)

    del charger.all_properties["amp"]

    assert entity.available is False


def test_attribute_source_availability(make_charger):
    """An attribute source depends on the attribute still being there."""
    charger = make_charger(props=dict(BASE_PROPS), car_connected=1)
    entity = _build(charger, source="attribute", id="car_connected")

    assert entity.available is True
    del charger.car_connected
    assert entity.available is False


def test_namespacelist_source_availability(make_charger):
    """A namespacelist source depends on its indexed item existing."""
    charger = make_charger(props={**BASE_PROPS, "cards": [SimpleNamespace(energy=1)]})
    entity = _build(charger, source="namespacelist", id="cards_0", value_id="energy")

    assert entity.available is True
    charger.all_properties["cards"] = []
    assert entity.available is False


# --- polling decisions --------------------------------------------------------


def test_property_entity_polls_until_it_has_a_value(make_charger):
    """A property entity polls while unseeded and stops once pushed to."""
    entity = _build(make_charger(props=dict(BASE_PROPS)))

    assert entity.should_poll is True
    entity.state = 6
    assert entity.should_poll is False


def test_property_entity_polls_again_at_its_default_state(make_charger):
    """A value equal to the configured default counts as unseeded."""
    entity = _build(make_charger(props=dict(BASE_PROPS)), default_state=999)

    entity.state = 999
    assert entity.should_poll is True


def test_attribute_entity_always_polls(make_charger):
    """Attribute sources have no push channel, so they always poll."""
    charger = make_charger(props=dict(BASE_PROPS), car_connected=1)
    entity = _build(charger, source="attribute", id="car_connected")

    entity.state = 1
    assert entity.should_poll is True


# --- value handling -----------------------------------------------------------


async def test_namespace_value_uses_value_id_and_attribute_ids(make_charger):
    """A namespace value yields one state plus the configured attributes."""
    charger = make_charger(props={**BASE_PROPS, "cards": [SimpleNamespace(energy=5, name="Card A")]})
    entity = _build(charger, source="namespacelist", id="cards_0", value_id="energy", attribute_ids=["name"])

    state = await entity._async_update_validate_property(SimpleNamespace(energy=5, name="Card A"))

    assert state == 5
    assert entity._attributes["name"] == "Card A"


async def test_namespace_value_without_value_id_is_reported(make_charger, caplog):
    """A namespace source with no value_id cannot produce a state."""
    charger = make_charger(props={**BASE_PROPS, "cards": [SimpleNamespace(energy=5)]})
    entity = _build(charger, source="namespacelist", id="cards_0", value_id="energy")
    entity._entity_cfg = {"source": "namespacelist", "id": "cards_0"}

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"):
        assert await entity._async_update_validate_property(SimpleNamespace(energy=5)) is None

    assert any("please specify the 'value_id'" in r.getMessage() for r in caplog.records)


async def test_list_value_without_value_id_spreads_into_attributes(make_charger):
    """A list value uses its first item as state and the rest as attributes."""
    charger = make_charger(props={**BASE_PROPS, "nrg": [1, 2, 3]})
    entity = _build(charger, id="nrg")

    state = await entity._async_update_validate_property([1, 2, 3])

    assert state == 1
    assert entity._attributes["state1"] == 2
    assert entity._attributes["state2"] == 3


async def test_list_value_with_value_id_picks_an_index(make_charger):
    """A list value with value_id picks one index and names the others."""
    charger = make_charger(props={**BASE_PROPS, "nrg": [1, 2, 3]})
    entity = _build(charger, id="nrg", value_id=1, attribute_ids=["L1:0", "L3:2"])

    state = await entity._async_update_validate_property([1, 2, 3])

    assert state == 2
    assert entity._attributes["L1"] == 1
    assert entity._attributes["L3"] == 3


async def test_value_validation_failure_is_logged(make_charger, caplog):
    """An index outside the list is logged and yields no state."""
    charger = make_charger(props={**BASE_PROPS, "nrg": [1]})
    entity = _build(charger, id="nrg", value_id=5)

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"):
        assert await entity._async_update_validate_property([1]) is None

    assert any("_async_update_validate_property failed" in r.getMessage() for r in caplog.records)


# --- poll and push ------------------------------------------------------------


async def test_poll_reads_a_property(make_charger):
    """Polling a property source writes the charger value into the state."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity.async_write_ha_state = MagicMock()

    await entity.async_local_poll()

    assert entity.state == 6
    entity.async_write_ha_state.assert_called_once()


async def test_poll_reads_an_attribute(make_charger):
    """Polling an attribute source reads it off the charger object."""
    charger = make_charger(props=dict(BASE_PROPS), car_connected=3)
    entity = _build(charger, source="attribute", id="car_connected")
    entity.async_write_ha_state = MagicMock()

    await entity.async_local_poll()

    assert entity.state == 3


async def test_poll_reads_a_namespacelist_item(make_charger):
    """Polling a namespacelist source resolves the item and its value_id."""
    charger = make_charger(props={**BASE_PROPS, "cards": [SimpleNamespace(energy=7)]})
    entity = _build(charger, source="namespacelist", id="cards_0", value_id="energy")
    entity.async_write_ha_state = MagicMock()

    await entity.async_local_poll()

    assert entity.state == 7


async def test_poll_failure_is_logged(make_charger, caplog):
    """A failure while writing the state is logged, not raised."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity.async_write_ha_state = MagicMock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"):
        await entity.async_local_poll()

    assert any("async_local_poll failed" in r.getMessage() for r in caplog.records)


async def test_push_writes_the_pushed_value(make_charger):
    """A pushed value becomes the entity state without touching the charger."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity.async_write_ha_state = MagicMock()

    await entity.async_local_push(16)

    assert entity.state == 16


async def test_push_falls_back_to_polling(make_charger):
    """A pushed value that validates to nothing triggers a poll instead."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity.async_write_ha_state = MagicMock()

    await entity.async_local_push(None)

    assert entity.state == 6


async def test_push_is_skipped_for_disabled_entities(make_charger):
    """A disabled entity ignores pushes."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    with patch.object(type(entity), "enabled", property(lambda self: False)):
        assert await entity.async_local_push(16) is None
    assert entity.state == STATE_UNKNOWN


async def test_push_failure_is_logged(make_charger, caplog):
    """A failure while pushing is logged, not raised."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity.async_write_ha_state = MagicMock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"):
        await entity.async_local_push(16)

    assert any("async_local_push failed" in r.getMessage() for r in caplog.records)


# --- async_update -------------------------------------------------------------


async def test_update_polls_when_polling_is_due(make_charger):
    """async_update delegates to the poll path while the entity is unseeded."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity.async_write_ha_state = MagicMock()

    await entity.async_update()

    assert entity.state == 6


async def test_update_does_nothing_once_pushes_arrive(make_charger):
    """async_update is a no-op for an entity that receives pushes."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity.state = 16
    entity.async_write_ha_state = MagicMock()

    await entity.async_update()

    assert entity.state == 16
    entity.async_write_ha_state.assert_not_called()


async def test_update_skips_unavailable_entities(make_charger):
    """An unavailable entity is not polled."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)
    entity.async_write_ha_state = MagicMock()
    charger.connected = False

    await entity.async_update()

    entity.async_write_ha_state.assert_not_called()


async def test_update_failure_is_logged(make_charger, caplog):
    """An unexpected failure during update is logged, not raised."""
    charger = make_charger(props=dict(BASE_PROPS))
    entity = _build(charger)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.entities"),
        patch.object(type(entity), "available", property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))),
    ):
        await entity.async_update()

    assert any("async_update failed" in r.getMessage() for r in caplog.records)
