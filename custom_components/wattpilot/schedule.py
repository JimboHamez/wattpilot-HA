"""Decode and build the charger's charging-schedule objects.

The app's charging-times screen is stored on the charger as three read/write
properties, one per day type - ``sch_week``, ``sch_satur`` and ``sch_sund`` -
each ``{control, ranges: [{begin: {hour, minute, second}, end: {...}}]}``.
The client hands them over as nested ``SimpleNamespace`` objects and accepts
a plain nested dict back; a nested value cannot be partially written, so a
change always rewrites the whole object.

``control`` is a bitmask on Fronius firmware, not the ``Disabled=0 / Inside=1
/ Outside=2`` enum the API definition documents: bit 0 limits charging to the
ranges ("Limit charging times") and bit 1 allows PV-surplus charging outside
them ("Charge with PV surplus"). Observed on firmware 43.4, where the charger
reports ``3`` with both toggles on and ``1`` once PV surplus is switched off.
Values outside the two known bits are decoded from those bits and exposed raw
rather than rejected.
"""

from __future__ import annotations

import datetime
from itertools import pairwise
from typing import Any, Final

# The property behind each day type of the app's charging-times screen.
SCHEDULE_PROPS: Final = {"weekdays": "sch_week", "saturday": "sch_satur", "sunday": "sch_sund"}

CONTROL_LIMIT_TIMES: Final = 1
CONTROL_PV_SURPLUS_OUTSIDE: Final = 2
CONTROL_MASK: Final = CONTROL_LIMIT_TIMES | CONTROL_PV_SURPLUS_OUTSIDE

ATTR_CONTROL: Final = "control"
ATTR_LIMIT_CHARGING_TIMES: Final = "limit_charging_times"
ATTR_PV_SURPLUS_OUTSIDE_TIMES: Final = "pv_surplus_outside_times"
ATTR_RANGES: Final = "ranges"
ATTR_BEGIN: Final = "begin"
ATTR_END: Final = "end"


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Return a field of a charger object, whether it arrived as a namespace or a dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_time(value: Any) -> datetime.time:
    """Return a time of day parsed from a service parameter.

    Args:
        value: A ``datetime.time``, or a string in ``HH:MM`` or ``HH:MM:SS`` form.

    Returns:
        The parsed time of day.

    Raises:
        ValueError: If the value is neither a time nor a string in one of the
            accepted forms.
    """
    if isinstance(value, datetime.time):
        return value
    if not isinstance(value, str):
        raise ValueError(f"not a time of day: {value!r}")
    return datetime.time.fromisoformat(value.strip())


def format_time(value: Any) -> str:
    """Return a charger ``{hour, minute, second}`` object as ``HH:MM`` (``HH:MM:SS`` when seconds are set).

    Args:
        value: The charger's time object, as a namespace or a dict.

    Returns:
        The time of day as text, the way the app shows it.
    """
    parsed = datetime.time(
        int(_field(value, "hour", 0) or 0),
        int(_field(value, "minute", 0) or 0),
        int(_field(value, "second", 0) or 0),
    )
    return parsed.isoformat(timespec="seconds" if parsed.second else "minutes")


def decode_schedule(value: Any) -> dict[str, Any] | None:
    """Return a charger schedule object in the form the sensor and the service work with.

    Args:
        value: The raw property value (a namespace from the client, or a dict).

    Returns:
        ``control`` (raw), the two decoded flags, and ``ranges`` as a list of
        ``{"begin": "HH:MM", "end": "HH:MM"}`` dicts - or None when the value
        carries no ``control`` field (a null property, or something else).
    """
    control = _field(value, ATTR_CONTROL)
    if control is None:
        return None
    control = int(control)
    ranges = [
        {ATTR_BEGIN: format_time(_field(item, ATTR_BEGIN)), ATTR_END: format_time(_field(item, ATTR_END))}
        for item in (_field(value, ATTR_RANGES) or [])
    ]
    return {
        ATTR_CONTROL: control,
        ATTR_LIMIT_CHARGING_TIMES: bool(control & CONTROL_LIMIT_TIMES),
        ATTR_PV_SURPLUS_OUTSIDE_TIMES: bool(control & CONTROL_PV_SURPLUS_OUTSIDE),
        ATTR_RANGES: ranges,
    }


class ScheduleRangeError(ValueError):
    """A charging window is unusable; ``reason`` names which rule it broke.

    ``crosses_midnight``: the window does not end after it begins on the same
    day, so it would run into the next day type (which has its own schedule).
    ``overlap``: two windows share time. ``ranges`` carries the offending
    window(s) as ``HH:MM-HH:MM`` text for the error message.
    """

    def __init__(self, reason: str, ranges: list[str]) -> None:
        """Initialize the error with the rule broken and the windows involved."""
        super().__init__(f"{reason}: {', '.join(ranges)}")
        self.reason = reason
        self.ranges = ranges


def _range_text(begin: datetime.time, end: datetime.time) -> str:
    """Return a window as ``HH:MM-HH:MM`` for messages."""
    return f"{begin.isoformat(timespec='minutes')}-{end.isoformat(timespec='minutes')}"


def validate_ranges(ranges: list[tuple[datetime.time, datetime.time]]) -> None:
    """Check that charging windows stay within one day and do not overlap.

    Each day type has its own schedule, so a window that does not end after it
    begins (``22:00-06:00``, or an empty ``08:00-08:00``) would cross midnight
    into the next day type's schedule and is rejected; the app splits such a
    window into an evening and a morning one. Windows may touch (``06:00-08:00``
    then ``08:00-10:00``) but not overlap.

    Args:
        ranges: The windows as (begin, end) times of day.

    Raises:
        ScheduleRangeError: If a window crosses midnight or two windows overlap.
    """
    for begin, end in ranges:
        if end <= begin:
            raise ScheduleRangeError("crosses_midnight", [_range_text(begin, end)])
    ordered = sorted(ranges)
    for (begin_a, end_a), (begin_b, end_b) in pairwise(ordered):
        if begin_b < end_a:
            raise ScheduleRangeError("overlap", [_range_text(begin_a, end_a), _range_text(begin_b, end_b)])


def build_schedule(
    limit_charging_times: bool,
    pv_surplus_outside_times: bool,
    ranges: list[tuple[datetime.time, datetime.time]],
) -> dict[str, Any]:
    """Return the object to write to a schedule property.

    Args:
        limit_charging_times: Whether charging is limited to the ranges (bit 0).
        pv_surplus_outside_times: Whether PV-surplus charging is allowed outside
            the ranges (bit 1).
        ranges: The charging windows as (begin, end) times of day.

    Returns:
        The nested dict the client sends as the property's JSON value.
    """
    control = (CONTROL_LIMIT_TIMES if limit_charging_times else 0) | (
        CONTROL_PV_SURPLUS_OUTSIDE if pv_surplus_outside_times else 0
    )
    return {
        ATTR_CONTROL: control,
        ATTR_RANGES: [
            {
                ATTR_BEGIN: {"hour": begin.hour, "minute": begin.minute, "second": begin.second},
                ATTR_END: {"hour": end.hour, "minute": end.minute, "second": end.second},
            }
            for begin, end in ranges
        ],
    }
