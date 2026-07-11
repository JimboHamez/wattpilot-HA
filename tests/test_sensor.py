"""Tests for sensor-platform value handling.

Focuses on timestamp parsing: a 'timestamp' device_class sensor must expose a
timezone-aware datetime, so the charger's string clock value has to be parsed
(see the 'loc' / Local Time sensor). Importing the module pulls in Home
Assistant and the vendored wattpilot library; skip cleanly if unavailable.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

try:
    from custom_components.wattpilot.sensor import ChargerSensor
except ImportError as exc:
    pytest.skip(f"integration import unavailable: {exc}", allow_module_level=True)


def _parse(value):
    # _parse_timestamp only uses self for debug logging, so a light stub is fine.
    stub = SimpleNamespace(_charger_id="wp", _identifier="loc")
    return ChargerSensor._parse_timestamp(stub, value)


def test_parses_charger_string_with_space_before_offset():
    # The exact shape the charger emits (space before the UTC offset).
    result = _parse("2026-07-12T01:41:26.437 +10:00")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 10 * 3600


def test_parses_standard_iso_without_space():
    result = _parse("2026-07-12T01:41:26+10:00")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_naive_value_gets_default_timezone():
    result = _parse("2026-07-12T01:41:26")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None  # filled in with HA's default zone


def test_datetime_passed_through():
    now = datetime.now().astimezone()
    assert _parse(now) is now


def test_unparseable_returns_none():
    assert _parse("not a timestamp") is None
