"""Regression tests for how an entity is identified and how it starts up.

Two startup bugs are covered here, both reported from a live charger:

- The entity ``id`` was split on '_' for every source, so the attribute sensors
  'access_state' / 'car_connected' looked for an 'access' / 'car' attribute that
  does not exist on the charger and were dropped at setup. Only a namespacelist
  id ('cards_0') carries an index suffix.
- The first state Home Assistant writes happens at add time, before any poll or
  push. Writing the STATE_UNKNOWN *string* made HA reject enum sensors ("not in
  the list of options") and timestamp sensors ("has timestamp device class but
  provides state unknown"), so those entities failed to be added at all.
"""

from __future__ import annotations

import pytest

try:
    from homeassistant.const import STATE_UNKNOWN

    from custom_components.wattpilot.entities import ChargerPlatformEntity
    from custom_components.wattpilot.sensor import ChargerSensor
except ImportError as exc:
    pytest.skip(f"integration import unavailable: {exc}", allow_module_level=True)


class _Entry:
    """Minimal ConfigEntry stand-in (only .data is read during __init__)."""

    entry_id = "test-entry"
    data: dict = {}


def _build(cls, cfg, charger):
    return cls(None, _Entry(), dict(cfg), charger)


def test_attribute_id_with_underscore_is_not_split(make_charger):
    """'car_connected' must resolve to the attribute of that exact name."""
    charger = make_charger(props={}, car_connected="ready")
    entity = _build(ChargerSensor, {"source": "attribute", "id": "car_connected"}, charger)

    assert entity._identifier == "car_connected"
    assert entity._init_failed is False


def test_namespacelist_id_keeps_its_index_suffix_stripped(make_charger):
    """'cards_0' still addresses the 'cards' property, item 0."""
    charger = make_charger(props={"cards": [{"energy": 1}]})
    entity = _build(
        ChargerSensor,
        {"source": "namespacelist", "id": "cards_0", "namespace_id": 0, "value_id": "energy"},
        charger,
    )

    assert entity._identifier == "cards"


def test_enum_sensor_starts_at_none_not_the_unknown_string(make_charger):
    """An enum sensor must not start at 'unknown': HA rejects it as an option."""
    charger = make_charger(props={"modelStatus": 2})
    entity = _build(
        ChargerSensor,
        {"source": "property", "id": "modelStatus", "enum": {1: "Idle", 2: "Charging"}},
        charger,
    )

    assert entity._attr_native_value is None
    assert STATE_UNKNOWN not in (entity._attr_options or [])


def test_timestamp_sensor_starts_at_none(make_charger):
    """A timestamp sensor must not start at 'unknown': HA demands a datetime."""
    charger = make_charger(props={"loc": "2026-07-12T01:41:26.437 +10:00"})
    entity = _build(ChargerSensor, {"source": "property", "id": "loc", "device_class": "timestamp"}, charger)

    assert entity._attr_native_value is None


def test_explicit_default_state_is_still_honoured(make_charger):
    """A yaml default_state still wins over the None start."""
    charger = make_charger(props={"eto": 12345})
    entity = _build(ChargerSensor, {"source": "property", "id": "eto", "default_state": -1}, charger)

    assert entity._attr_native_value == -1


def test_enum_default_state_is_not_written_raw(make_charger):
    """trx's 999 sentinel is a charger code, not an HA option: it must not be the first state.

    HA rejected it at add time with "provides state value '999', which is not in
    the list of options provided", dropping the entity.
    """
    charger = make_charger(props={"trx": 999})
    entity = _build(
        ChargerSensor,
        {
            "source": "property",
            "id": "trx",
            "default_state": 999,
            "enum": {0: "No Chip", 999: "No Transaction"},
        },
        charger,
    )

    assert entity._attr_native_value is None
    assert "no_transaction" in entity._attr_options


@pytest.mark.asyncio
async def test_attribute_enum_sensor_maps_raw_code_to_slug(make_charger):
    """wattpilot-api returns raw ints for car_connected / access_state."""
    charger = make_charger(props={}, car_connected=1)
    entity = _build(
        ChargerSensor,
        {"source": "attribute", "id": "car_connected", "enum": {1: "No Car", 2: "Charging"}},
        charger,
    )

    assert await entity._async_update_validate_platform_state(1) == "no_car"


def test_none_state_still_polls(make_charger):
    """Starting at None must not switch polling off, or nothing seeds the value."""
    charger = make_charger(props={"loc": "2026-07-12T01:41:26.437 +10:00"})
    entity = _build(ChargerSensor, {"source": "property", "id": "loc", "device_class": "timestamp"}, charger)

    assert ChargerPlatformEntity.should_poll.fget(entity) is True
