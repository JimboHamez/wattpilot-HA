"""Tests for what the platform entities do with charger values.

Entities are built straight from their YAML definition (no Home Assistant
platform setup) so each platform's write path, state coercion and failure
handling can be driven directly.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

pytest.importorskip("wattpilot_api", reason="integration import unavailable")
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN

from custom_components.wattpilot import button as button_mod
from custom_components.wattpilot.button import ChargerButton
from custom_components.wattpilot.number import ChargerNumber
from custom_components.wattpilot.select import ChargerSelect
from custom_components.wattpilot.sensor import ChargerSensor
from custom_components.wattpilot.switch import ChargerSwitch
from custom_components.wattpilot.update import ChargerUpdate

COMPONENT_DIR = os.path.dirname(button_mod.__file__)


def _catalog(platform: str) -> dict:
    """Return the platform's catalog definitions keyed by uid (or id)."""
    with open(os.path.join(COMPONENT_DIR, f"{platform}.yaml"), encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    return {item.get("uid", item["id"]): item for item in cfg[platform]}


def _build(entity_class, platform: str, key: str, charger, **overrides):
    """Build one entity from its catalog definition."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {"friendly_name": "WB"}
    entry.runtime_data = {}
    cfg = dict(_catalog(platform)[key])
    cfg.setdefault("source", "property")
    cfg.update(overrides)
    return entity_class(hass, entry, cfg, charger)


# --- button -------------------------------------------------------------------


async def test_button_press_writes_its_configured_value(make_charger):
    """Pressing a button writes the value from its definition."""
    charger = make_charger(props={"frc": 0, "typ": "m", "var": 11})
    entity = _build(ChargerButton, "button", "frc1", charger)

    await entity.async_press()

    assert charger.sent[-1] == ("frc", 1)


async def test_button_press_failure_is_logged(make_charger, caplog):
    """A charger that rejects the write is logged, not raised."""
    charger = make_charger(props={"frc": 0, "typ": "m", "var": 11})
    entity = _build(ChargerButton, "button", "frc1", charger)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.button"),
        patch("custom_components.wattpilot.button.async_SetChargerProp", side_effect=RuntimeError("boom")),
    ):
        await entity.async_press()

    assert any("update failed" in r.getMessage() for r in caplog.records)


async def test_button_without_a_set_value_is_reported(make_charger, caplog):
    """A definition missing 'set_value' is reported at construction."""
    charger = make_charger(props={"frc": 0, "typ": "m", "var": 11})

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.button"):
        _build(ChargerButton, "button", "frc1", charger, set_value=None)

    assert any("'set_value' missing" in r.getMessage() for r in caplog.records)


async def test_button_does_not_poll(make_charger):
    """Buttons hold no state, so polling is a no-op."""
    charger = make_charger(props={"frc": 0, "typ": "m", "var": 11})
    entity = _build(ChargerButton, "button", "frc1", charger)

    assert await entity.async_local_poll() is None


# --- switch -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, STATE_ON), ("true", STATE_ON), (1, STATE_ON), (False, STATE_OFF), ("false", STATE_OFF), (0, STATE_OFF)],
)
async def test_switch_maps_charger_values_to_states(make_charger, raw, expected):
    """Truthy and falsy charger values become on/off states."""
    charger = make_charger(props={"fup": False, "typ": "m", "var": 11})
    entity = _build(ChargerSwitch, "switch", "fup", charger)

    assert await entity._async_update_validate_platform_state(raw) == expected


async def test_switch_reports_an_unmappable_value(make_charger, caplog):
    """A value that is neither true nor false becomes unknown, with a warning."""
    charger = make_charger(props={"fup": False, "typ": "m", "var": 11})
    entity = _build(ChargerSwitch, "switch", "fup", charger)

    with caplog.at_level(logging.WARNING, logger="custom_components.wattpilot.switch"):
        assert await entity._async_update_validate_platform_state("maybe") == STATE_UNKNOWN

    assert any("not valid for switch platform" in r.getMessage() for r in caplog.records)


async def test_switch_passes_through_unknown(make_charger):
    """An unknown state stays unknown instead of being coerced."""
    charger = make_charger(props={"fup": False, "typ": "m", "var": 11})
    entity = _build(ChargerSwitch, "switch", "fup", charger)

    assert await entity._async_update_validate_platform_state(STATE_UNKNOWN) == STATE_UNKNOWN


async def test_inverted_switch_flips_both_states(make_charger):
    """An inverted switch reports the opposite of the charger value."""
    charger = make_charger(props={"fup": False, "typ": "m", "var": 11})
    entity = _build(ChargerSwitch, "switch", "fup", charger, invert=True)

    assert await entity._async_update_validate_platform_state(True) == STATE_OFF
    assert await entity._async_update_validate_platform_state(False) == STATE_ON


async def test_switch_turn_on_and_off_write_booleans(make_charger):
    """Turning a switch on and off writes true and false."""
    charger = make_charger(props={"fup": False, "typ": "m", "var": 11})
    entity = _build(ChargerSwitch, "switch", "fup", charger)

    await entity.async_turn_on()
    assert charger.sent[-1] == ("fup", True)
    await entity.async_turn_off()
    assert charger.sent[-1] == ("fup", False)


async def test_inverted_switch_writes_inverted_booleans(make_charger):
    """An inverted switch writes the opposite value to the charger."""
    charger = make_charger(props={"fup": False, "typ": "m", "var": 11})
    entity = _build(ChargerSwitch, "switch", "fup", charger, invert=True)

    await entity.async_turn_on()
    assert charger.sent[-1] == ("fup", False)
    await entity.async_turn_off()
    assert charger.sent[-1] == ("fup", True)


async def test_switch_is_on_follows_the_state(make_charger):
    """is_on mirrors the entity state."""
    charger = make_charger(props={"fup": False, "typ": "m", "var": 11})
    entity = _build(ChargerSwitch, "switch", "fup", charger)

    entity.state = STATE_ON
    assert entity.is_on is True
    entity.state = STATE_OFF
    assert entity.is_on is False


@pytest.mark.parametrize("method", ["async_turn_on", "async_turn_off"])
async def test_switch_write_failure_is_logged(make_charger, caplog, method):
    """A failing write is logged rather than raised at the caller."""
    charger = make_charger(props={"fup": False, "typ": "m", "var": 11})
    entity = _build(ChargerSwitch, "switch", "fup", charger)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.switch"),
        patch("custom_components.wattpilot.switch.async_SetChargerProp", side_effect=RuntimeError("boom")),
    ):
        await getattr(entity, method)()

    assert any(f"{method} failed" in r.getMessage() for r in caplog.records)


# --- number -------------------------------------------------------------------


async def test_number_scales_raw_values_on_read(make_charger):
    """A factor converts the charger's raw unit into the entity's unit."""
    charger = make_charger(props={"fmt": 600000, "typ": "m", "var": 11})
    entity = _build(ChargerNumber, "number", "fmt", charger)

    # 'fmt' is milliseconds on the charger, minutes on the entity (factor 60000).
    assert await entity._async_update_validate_platform_state(600000) == 10


async def test_number_scales_values_back_on_write(make_charger):
    """Setting a value multiplies it back into the charger's raw unit."""
    charger = make_charger(props={"fmt": 0, "typ": "m", "var": 11})
    entity = _build(ChargerNumber, "number", "fmt", charger)

    await entity.async_set_native_value(10)

    assert charger.sent[-1] == ("fmt", 600000)


async def test_number_without_a_factor_writes_the_value_unchanged(make_charger):
    """An unscaled number writes exactly what it was given."""
    charger = make_charger(props={"amp": 6, "typ": "m", "var": 11})
    entity = _build(ChargerNumber, "number", "amp", charger, variant=None)

    await entity.async_set_native_value(16)

    assert charger.sent[-1] == ("amp", 16)


async def test_next_trip_energy_forces_kwh_mode(make_charger):
    """Writing the next-trip energy target also forces the kWh flag."""
    charger = make_charger(props={"fte": 0, "esk": False, "typ": "m", "var": 11})
    entity = _build(ChargerNumber, "number", "fte", charger)

    await entity.async_set_native_value(20)

    assert ("esk", True) in charger.sent
    assert charger.sent[-1] == ("fte", 20000)


async def test_number_write_failure_is_logged(make_charger, caplog):
    """A failing write is logged rather than raised at the caller."""
    charger = make_charger(props={"amp": 6, "typ": "m", "var": 11})
    entity = _build(ChargerNumber, "number", "amp", charger, variant=None)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.number"),
        patch("custom_components.wattpilot.number.async_SetChargerProp", side_effect=RuntimeError("boom")),
    ):
        await entity.async_set_native_value(16)

    assert any("update failed" in r.getMessage() for r in caplog.records)


# --- sensor -------------------------------------------------------------------


async def test_sensor_maps_enum_values_to_slugs(make_charger):
    """An enum sensor turns the charger's raw code into its option slug."""
    charger = make_charger(props={"car": 1, "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "car", charger)

    assert await entity._async_update_validate_platform_state(1) == "idle"


async def test_sensor_rejects_a_value_outside_its_enum(make_charger):
    """An unmapped code yields no state rather than an invalid one."""
    charger = make_charger(props={"car": 1, "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "car", charger)

    assert await entity._async_update_validate_platform_state(99) is None


async def test_sensor_keeps_plain_numeric_values(make_charger):
    """A plain sensor passes its value through untouched."""
    charger = make_charger(props={"tma": 5.0, "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "tma", charger)

    assert await entity._async_update_validate_platform_state(21.5) == 21.5


# --- update -------------------------------------------------------------------


def _update_entity(charger, **overrides):
    """Build the firmware update entity."""
    return _build(ChargerUpdate, "update", next(iter(_catalog("update"))), charger, **overrides)


async def test_update_reports_installed_and_latest_version(make_charger):
    """The entity reads the installed version and the newest available one."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5", "40.1"], "typ": "m", "var": 11})
    entity = _update_entity(charger)

    assert entity._attr_installed_version == "38.5"
    assert entity._attr_latest_version == "40.1"


async def test_update_wraps_a_single_version_into_a_list(make_charger):
    """A charger reporting one version instead of a list is still handled."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5"], "typ": "m", "var": 11})
    entity = _update_entity(charger)

    assert entity._update_available_versions("41.0") == "41.0"


async def test_update_reports_unsortable_versions(make_charger, caplog):
    """Version names that cannot be ordered fall back to the dummy version."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5"], "typ": "m", "var": 11})
    entity = _update_entity(charger)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.update"),
        patch.object(entity, "_get_versions_dict", return_value={"not-a-version": "x"}),
    ):
        assert entity._update_available_versions(["x"], True) == entity._dummy_version
        assert entity._update_available_versions(["x"]) is None

    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_update_version_cleanup_failure_is_logged(make_charger, caplog):
    """A version list that cannot be parsed at all is logged, not raised."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5"], "typ": "m", "var": 11})
    entity = _update_entity(charger)

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.update"):
        assert entity._get_versions_dict([object()]) is None

    assert any("_get_versions_dict failed" in r.getMessage() for r in caplog.records)


async def test_update_cleans_version_names_for_sorting(make_charger):
    """Free-form charger version names are normalised before comparison."""
    charger = make_charger(props={"fwv": "38.5", "typ": "m", "var": 11})
    entity = _update_entity(charger)

    assert entity._get_versions_dict(["V1.2.3-beta4"]) == {"1.2.3beta4": "V1.2.3-beta4"}


async def test_update_install_triggers_the_charger(make_charger):
    """Installing writes the raw version name to the trigger property."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5", "40.1"], "typ": "m", "var": 11})
    entity = _update_entity(charger)
    # The charger drops its connection while flashing and comes back after.
    charger.connected = False

    with patch("custom_components.wattpilot.update.asyncio.sleep", new=AsyncMock()):
        await entity.async_install("40.1", backup=False)

    assert any(identifier == "oct" and value == "40.1" for identifier, value in charger.sent)


async def test_update_install_rejects_an_unknown_version(make_charger, caplog):
    """A version the charger does not offer is refused before writing."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5"], "typ": "m", "var": 11})
    entity = _update_entity(charger)

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.update"):
        await entity.async_install("99.9", backup=False)

    assert charger.sent == []
    assert any("not in available" in r.getMessage() for r in caplog.records)


async def test_update_install_reports_a_charger_that_never_disconnects(make_charger, caplog):
    """A charger still connected after the timeout is reported as a failure."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5", "40.1"], "typ": "m", "var": 11})
    entity = _update_entity(charger)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.update"),
        patch("custom_components.wattpilot.update.asyncio.sleep", new=AsyncMock()),
    ):
        await entity.async_install("40.1", backup=False)

    assert any("timeout during update install" in r.getMessage() for r in caplog.records)


async def test_update_install_reports_a_charger_that_never_returns(make_charger, caplog):
    """A charger that stays offline after flashing is reported as a failure."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5", "40.1"], "typ": "m", "var": 11})
    entity = _update_entity(charger)
    charger.connected = False

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.update"),
        patch("custom_components.wattpilot.update.asyncio.sleep", new=AsyncMock()),
    ):
        await entity.async_install("40.1", backup=False)

    assert any("timeout during charger restart" in r.getMessage() for r in caplog.records)


# --- select -------------------------------------------------------------------


async def test_select_rejects_a_value_outside_its_options(make_charger, caplog):
    """A raw value with no matching option leaves the selection unchanged."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11})
    entity = _build(ChargerSelect, "select", "lmo", charger)

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.select"):
        assert await entity._async_update_validate_platform_state(99) is None

    assert any("not within options" in r.getMessage() for r in caplog.records)


async def test_select_accepts_an_already_mapped_slug(make_charger):
    """A slug that is already an option is passed through unchanged."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11})
    entity = _build(ChargerSelect, "select", "lmo", charger)

    assert await entity._async_update_validate_platform_state("eco") == "eco"


async def test_select_state_validation_failure_is_logged(make_charger, caplog):
    """An unusable option mapping is logged rather than raised."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11})
    entity = _build(ChargerSelect, "select", "lmo", charger)
    entity._opt_dict = None  # breaks the membership test

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.select"):
        assert await entity._async_update_validate_platform_state(3) is None

    assert any("_async_update_validate_platform_state failed" in r.getMessage() for r in caplog.records)


async def test_select_rejects_an_unknown_option(make_charger, caplog):
    """Selecting an option the charger does not know writes nothing."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11})
    entity = _build(ChargerSelect, "select", "lmo", charger)

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.select"):
        await entity.async_select_option("teleport")

    assert charger.sent == []
    assert any("not within options" in r.getMessage() for r in caplog.records)


async def test_select_write_failure_is_logged(make_charger, caplog):
    """A failing write is logged rather than raised at the caller."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11})
    entity = _build(ChargerSelect, "select", "lmo", charger)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.select"),
        patch("custom_components.wattpilot.select.async_SetChargerProp", side_effect=RuntimeError("boom")),
    ):
        await entity.async_select_option("eco")

    assert any("async_select_option failed" in r.getMessage() for r in caplog.records)


async def test_select_reads_dynamic_options_from_the_charger(make_charger):
    """Options named by an attribute are read off the charger object."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11}, lmoValues={3: "Default", 4: "Eco"})
    entity = _build(ChargerSelect, "select", "lmo", charger, options="lmoValues")

    assert entity._attr_options == ["Default", "Eco"]
    assert await entity._async_update_validate_platform_state(4) == "Eco"


async def test_select_without_usable_options_has_none(make_charger):
    """An attribute that is not an option mapping yields no options."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11})
    entity = _build(ChargerSelect, "select", "lmo", charger, options="doesNotExist")

    assert entity._attr_options == []


# --- sensor: timestamps, text and enums ---------------------------------------


async def test_timestamp_sensor_parses_the_charger_format(make_charger):
    """The charger's offset format (with a space) is parsed into a datetime."""
    charger = make_charger(props={"loc": "x", "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "loc", charger)

    parsed = await entity._async_update_validate_platform_state("2026-07-12T01:41:26.437 +10:00")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.year == 2026


async def test_timestamp_sensor_rejects_an_unparseable_value(make_charger):
    """A value that is not a timestamp yields no state."""
    charger = make_charger(props={"loc": "x", "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "loc", charger)

    assert await entity._async_update_validate_platform_state("not a date") is None
    assert await entity._async_update_validate_platform_state(None) is None


async def test_timestamp_sensor_passes_through_a_datetime(make_charger):
    """An already parsed datetime is used as-is."""
    from datetime import UTC, datetime

    charger = make_charger(props={"loc": "x", "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "loc", charger)
    moment = datetime(2026, 7, 12, tzinfo=UTC)

    assert await entity._async_update_validate_platform_state(moment) is moment


async def test_numeric_sensor_reports_a_missing_value_as_none(make_charger):
    """A numeric sensor cannot show the 'unknown' string, so it shows nothing."""
    charger = make_charger(props={"tma": 5.0, "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "tma", charger)

    assert await entity._async_update_validate_platform_state(None) is None
    assert await entity._async_update_validate_platform_state("None") is None


async def test_enum_sensor_accepts_an_already_mapped_slug(make_charger):
    """A slug that is already an option is passed through unchanged."""
    charger = make_charger(props={"car": 1, "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "car", charger)

    assert await entity._async_update_validate_platform_state("idle") == "idle"


async def test_sensor_state_validation_failure_is_logged(make_charger, caplog):
    """An unusable enum mapping is logged rather than raised."""
    charger = make_charger(props={"car": 1, "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "car", charger)
    entity._state_enum = None  # breaks the membership test

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.sensor"):
        assert await entity._async_update_validate_platform_state(1) is None

    assert any("_async_update_validate_platform_state failed" in r.getMessage() for r in caplog.records)


async def test_update_install_without_a_version_is_reported(make_charger, caplog):
    """An install with nothing to install is reported and does nothing."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5"], "typ": "m", "var": 11})
    entity = _update_entity(charger)
    entity._attr_latest_version = None

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.update"):
        await entity.async_install(None, backup=False)

    assert charger.sent == []
    assert any("no version to install" in r.getMessage() for r in caplog.records)


async def test_update_install_failure_is_logged(make_charger, caplog):
    """An unexpected failure during install is logged, not raised."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5", "40.1"], "typ": "m", "var": 11})
    entity = _update_entity(charger)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.update"),
        patch("custom_components.wattpilot.update.async_SetChargerProp", side_effect=RuntimeError("boom")),
    ):
        await entity.async_install("40.1", backup=False)

    assert any("async_install failed" in r.getMessage() for r in caplog.records)


async def test_update_state_validation_refreshes_both_versions(make_charger):
    """Validating a pushed value refreshes the installed and latest versions."""
    charger = make_charger(props={"fwv": "38.5", "onv": ["38.5"], "typ": "m", "var": 11})
    entity = _update_entity(charger)
    entity.hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))

    state = await entity._async_update_validate_platform_state(["38.5", "40.1"])

    assert state == "40.1"
    assert entity._attr_installed_version == "38.5"
