"""Tests for the charging-schedule codec and the entities and action built on it.

The app's charging times live on the charger as ``sch_week`` / ``sch_satur`` /
``sch_sund``, each an object whose ``control`` is a bitmask on Fronius firmware
(bit 0 = limit charging times, bit 1 = PV surplus outside the times) rather
than the Disabled/Inside/Outside enum the API definition documents. The codec
is tested on its own; the sensor and the ``set_charging_schedule`` action are
tested through the same paths the platform and Home Assistant use.
"""

from __future__ import annotations

import datetime
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("wattpilot_api", reason="integration import unavailable")

from custom_components.wattpilot import schedule
from custom_components.wattpilot.sensor import ChargerSensor

from .test_platform_entities import _build


def _time(hour: int, minute: int = 0, second: int = 0) -> SimpleNamespace:
    return SimpleNamespace(hour=hour, minute=minute, second=second)


def _raw_schedule(control: int = 3) -> SimpleNamespace:
    """A schedule the way the client hands it over: nested namespaces."""
    return SimpleNamespace(
        control=control,
        ranges=[
            SimpleNamespace(begin=_time(0), end=_time(6)),
            SimpleNamespace(begin=_time(8), end=_time(15)),
        ],
    )


# --- codec --------------------------------------------------------------------


def test_parse_time_accepts_times_and_iso_strings():
    """A time object passes through; HH:MM and HH:MM:SS strings are parsed."""
    assert schedule.parse_time(datetime.time(6, 30)) == datetime.time(6, 30)
    assert schedule.parse_time("06:30") == datetime.time(6, 30)
    assert schedule.parse_time(" 22:15:30 ") == datetime.time(22, 15, 30)


@pytest.mark.parametrize("value", ["half six", "25:00", 630, None])
def test_parse_time_rejects_anything_else(value):
    """Anything that is not a time of day raises ValueError."""
    with pytest.raises(ValueError):
        schedule.parse_time(value)


def test_format_time_shows_seconds_only_when_set():
    """Whole minutes read as HH:MM like the app; seconds appear only when non-zero."""
    assert schedule.format_time(_time(6)) == "06:00"
    assert schedule.format_time({"hour": 22, "minute": 15, "second": 30}) == "22:15:30"
    assert schedule.format_time(SimpleNamespace(hour=None, minute=None)) == "00:00"


def test_decode_schedule_reads_flags_and_windows():
    """control=3 is both flags on, and the windows come out as HH:MM begin/end pairs."""
    decoded = schedule.decode_schedule(_raw_schedule(3))

    assert decoded == {
        "control": 3,
        "limit_charging_times": True,
        "pv_surplus_outside_times": True,
        "ranges": [{"begin": "00:00", "end": "06:00"}, {"begin": "08:00", "end": "15:00"}],
    }


@pytest.mark.parametrize(
    ("control", "limit", "pv"),
    [(0, False, False), (1, True, False), (2, False, True), (7, True, True)],
)
def test_decode_schedule_treats_control_as_flags(control, limit, pv):
    """Each bit is read on its own, and unknown higher bits are kept in the raw value."""
    decoded = schedule.decode_schedule({"control": control, "ranges": []})

    assert decoded is not None
    assert decoded["control"] == control
    assert (decoded["limit_charging_times"], decoded["pv_surplus_outside_times"]) == (limit, pv)
    assert decoded["ranges"] == []


@pytest.mark.parametrize("value", [None, 3, "x", SimpleNamespace(ranges=[]), {}])
def test_decode_schedule_needs_a_control_field(value):
    """A null property or something that is not a schedule decodes to None."""
    assert schedule.decode_schedule(value) is None


@pytest.mark.parametrize(
    "ranges",
    [
        [(datetime.time(22, 0), datetime.time(6, 0))],
        [(datetime.time(8, 0), datetime.time(8, 0))],
        [(datetime.time(0, 0), datetime.time(6, 0)), (datetime.time(20, 0), datetime.time(0, 0))],
    ],
)
def test_validate_ranges_rejects_a_window_that_crosses_midnight(ranges):
    """A window must end after it begins on the same day; the next day type has its own schedule."""
    with pytest.raises(schedule.ScheduleRangeError) as err:
        schedule.validate_ranges(ranges)
    assert err.value.reason == "crosses_midnight"
    assert len(err.value.ranges) == 1


@pytest.mark.parametrize(
    "ranges",
    [
        [(datetime.time(0, 0), datetime.time(6, 0)), (datetime.time(5, 0), datetime.time(8, 0))],
        [(datetime.time(8, 0), datetime.time(15, 0)), (datetime.time(0, 0), datetime.time(9, 0))],
        [(datetime.time(8, 0), datetime.time(15, 0)), (datetime.time(9, 0), datetime.time(10, 0))],
        [(datetime.time(8, 0), datetime.time(15, 0)), (datetime.time(8, 0), datetime.time(15, 0))],
    ],
)
def test_validate_ranges_rejects_overlapping_windows(ranges):
    """Two windows that share time are rejected, whatever order they were given in."""
    with pytest.raises(schedule.ScheduleRangeError) as err:
        schedule.validate_ranges(ranges)
    assert err.value.reason == "overlap"
    assert len(err.value.ranges) == 2


def test_validate_ranges_accepts_touching_and_separate_windows():
    """Windows that touch or are apart, in any order, are fine - and so is no window at all."""
    schedule.validate_ranges([])
    schedule.validate_ranges([(datetime.time(0, 0), datetime.time(23, 59, 59))])
    schedule.validate_ranges(
        [
            (datetime.time(8, 0), datetime.time(15, 0)),
            (datetime.time(0, 0), datetime.time(6, 0)),
            (datetime.time(6, 0), datetime.time(8, 0)),
        ]
    )


def test_build_schedule_round_trips_through_decode():
    """The object built for the charger decodes back to what was asked for."""
    built = schedule.build_schedule(True, False, [(datetime.time(7, 30), datetime.time(17, 0, 15))])

    assert built == {
        "control": 1,
        "ranges": [
            {"begin": {"hour": 7, "minute": 30, "second": 0}, "end": {"hour": 17, "minute": 0, "second": 15}},
        ],
    }
    assert schedule.decode_schedule(built) == {
        "control": 1,
        "limit_charging_times": True,
        "pv_surplus_outside_times": False,
        "ranges": [{"begin": "07:30", "end": "17:00:15"}],
    }
    assert schedule.build_schedule(False, True, [])["control"] == 2
    assert schedule.build_schedule(False, False, [])["control"] == 0


# --- sensor -------------------------------------------------------------------


def _schedule_sensor(make_charger, key: str = "schedule_weekdays", **props):
    charger = make_charger(
        props={"sch_week": _raw_schedule(), "sch_satur": _raw_schedule(1), "typ": "m", "var": 11, **props}
    )
    return _build(ChargerSensor, "sensor", key, charger), charger


async def test_schedule_sensor_decodes_state_and_attributes(make_charger):
    """The state is the control flags as an enum option; the windows and flags are attributes."""
    entity, _charger = _schedule_sensor(make_charger)

    raw = await entity._async_update_validate_property(_raw_schedule(3))
    state = await entity._async_update_validate_platform_state(raw)

    assert state == "charging_times_pv_surplus_outside"
    assert entity._attributes["control"] == 3
    assert entity._attributes["limit_charging_times"] is True
    assert entity._attributes["pv_surplus_outside_times"] is True
    assert entity._attributes["ranges"] == [{"begin": "00:00", "end": "06:00"}, {"begin": "08:00", "end": "15:00"}]


@pytest.mark.parametrize(
    ("control", "option"),
    [(0, "off"), (1, "charging_times"), (2, "pv_surplus_outside_times"), (3, "charging_times_pv_surplus_outside")],
)
async def test_schedule_sensor_maps_each_control_value(make_charger, control, option):
    """All four flag combinations land on their enum option."""
    entity, _charger = _schedule_sensor(make_charger, "schedule_saturday")

    raw = await entity._async_update_validate_property(_raw_schedule(control))

    assert await entity._async_update_validate_platform_state(raw) == option


async def test_schedule_sensor_keeps_unknown_bits_on_an_option(make_charger):
    """A control with bits beyond the known two still maps, with the raw value as an attribute."""
    entity, _charger = _schedule_sensor(make_charger)

    raw = await entity._async_update_validate_property(_raw_schedule(7))

    assert raw == 3
    assert entity._attributes["control"] == 7
    assert await entity._async_update_validate_platform_state(raw) == "charging_times_pv_surplus_outside"


async def test_schedule_sensor_polls_the_charger_object(make_charger):
    """A poll reads the namespace from the charger and writes the decoded state."""
    entity, _charger = _schedule_sensor(make_charger)
    entity.async_write_ha_state = MagicMock()

    await entity.async_local_poll()

    assert entity._attr_native_value == "charging_times_pv_surplus_outside"
    assert entity._attributes["ranges"][0] == {"begin": "00:00", "end": "06:00"}
    entity.async_write_ha_state.assert_called_once()


async def test_schedule_sensor_ignores_a_null_property(make_charger):
    """A schedule the charger reports as null leaves the state alone."""
    entity, _charger = _schedule_sensor(make_charger)

    assert await entity._async_update_validate_property(None) is None
    assert "control" not in entity._attributes


async def test_schedule_sensor_decode_failure_is_logged(make_charger, caplog):
    """An unexpected decoding error is logged, not raised."""
    entity, _charger = _schedule_sensor(make_charger)

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.sensor"),
        patch("custom_components.wattpilot.sensor.decode_schedule", side_effect=RuntimeError("boom")),
    ):
        assert await entity._async_update_validate_property(_raw_schedule()) is None

    assert any("_async_update_validate_property failed" in r.getMessage() for r in caplog.records)


async def test_schedule_sensor_is_skipped_without_the_property(make_charger):
    """A charger without the schedule property does not get the entity."""
    charger = make_charger(props={"typ": "m", "var": 11})

    assert _build(ChargerSensor, "sensor", "schedule_sunday", charger)._init_failed is True


async def test_other_sensors_still_use_the_base_validation(make_charger):
    """A sensor without 'schedule' keeps the base namespace/list handling."""
    charger = make_charger(props={"rssi": -60, "typ": "m", "var": 11})
    entity = _build(ChargerSensor, "sensor", "rssi", charger)

    assert await entity._async_update_validate_property(-60) == -60


# --- utils: nested values reach the client untouched ---------------------------


async def test_set_charger_prop_sends_a_dict_as_is(make_charger):
    """A dict (the schedule object) is passed to the client as the JSON value, not stringified."""
    from custom_components.wattpilot import utils

    charger = make_charger(props={"sch_week": _raw_schedule()})
    payload = schedule.build_schedule(True, True, [(datetime.time(0), datetime.time(6))])

    assert await utils.async_SetChargerProp(charger, "sch_week", payload) is True

    assert charger.sent[-1] == ("sch_week", payload)
    assert isinstance(charger.sent[-1][1], dict)


async def test_set_charger_prop_sends_a_list_as_is(make_charger):
    """A list value is likewise handed over untouched."""
    from custom_components.wattpilot import utils

    charger = make_charger(props={"clp": [10, 16]})

    assert await utils.async_SetChargerProp(charger, "clp", [6, 10, 16]) is True
    assert charger.sent[-1] == ("clp", [6, 10, 16])


# --- action -------------------------------------------------------------------


async def test_service_handler_merges_supplied_fields_into_the_current_schedule(make_charger):
    """Only the supplied fields change; the rest of the object is rewritten from the charger."""
    from custom_components.wattpilot import services

    charger = make_charger(props={"sch_week": _raw_schedule(3)}, name="WB")
    call = MagicMock()
    call.data = {"device_id": "dev", "day_type": "weekdays", "pv_surplus_outside_times": False}

    with patch("custom_components.wattpilot.services._async_get_charger", new=AsyncMock(return_value=charger)):
        await services.async_service_SetChargingSchedule(MagicMock(), call)

    assert charger.sent[-1] == (
        "sch_week",
        {
            "control": 1,
            "ranges": [
                {"begin": {"hour": 0, "minute": 0, "second": 0}, "end": {"hour": 6, "minute": 0, "second": 0}},
                {"begin": {"hour": 8, "minute": 0, "second": 0}, "end": {"hour": 15, "minute": 0, "second": 0}},
            ],
        },
    )


async def test_service_handler_writes_new_ranges_and_string_booleans(make_charger):
    """Ranges replace the current windows, and "true"/"false" strings are accepted for the flags."""
    from custom_components.wattpilot import services

    charger = make_charger(props={"sch_sund": _raw_schedule(0)}, name="WB")
    call = MagicMock()
    call.data = {
        "device_id": "dev",
        "day_type": "Sunday",
        "limit_charging_times": "true",
        "ranges": [{"begin": "22:00", "end": "23:59:59"}],
    }

    with patch("custom_components.wattpilot.services._async_get_charger", new=AsyncMock(return_value=charger)):
        await services.async_service_SetChargingSchedule(MagicMock(), call)

    assert charger.sent[-1] == (
        "sch_sund",
        {
            "control": 1,
            "ranges": [
                {"begin": {"hour": 22, "minute": 0, "second": 0}, "end": {"hour": 23, "minute": 59, "second": 59}},
            ],
        },
    )
