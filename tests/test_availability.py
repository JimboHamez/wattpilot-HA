"""Tests for the connection availability logger.

Covers the quality-scale ``log-when-unavailable`` rule: a lost connection is
logged exactly once, and the recovery is logged once as well.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

pytest.importorskip("wattpilot_api", reason="integration import unavailable")
pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.wattpilot.availability import ChargerConnectionMonitor, charger_available
from custom_components.wattpilot.const import AVAILABILITY_SCAN_INTERVAL


def test_charger_available_reads_both_flags(mock_charger):
    """Availability requires both a live connection and initialized properties."""
    assert charger_available(mock_charger) is True

    mock_charger.connected = False
    assert charger_available(mock_charger) is False

    mock_charger.connected = True
    mock_charger.properties_initialized = False
    assert charger_available(mock_charger) is False


def test_charger_available_defaults_to_true_when_flags_absent():
    """A client not exposing the flags is not reported as offline."""

    class _Bare:
        pass

    assert charger_available(_Bare()) is True


async def test_logs_once_when_unavailable_and_once_on_recovery(hass, mock_charger, caplog):
    """The monitor logs each availability transition exactly once."""
    monitor = ChargerConnectionMonitor(hass, "entry-1", mock_charger)

    with caplog.at_level(logging.INFO, logger="custom_components.wattpilot.availability"):
        # Still connected: nothing to report.
        await monitor._async_check()
        assert caplog.records == []

        mock_charger.connected = False
        await monitor._async_check()
        # Repeated samples while offline must not repeat the warning.
        await monitor._async_check()
        await monitor._async_check()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "connection to charger lost" in warnings[0].getMessage()

        mock_charger.connected = True
        await monitor._async_check()
        await monitor._async_check()
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1
        assert "re-established" in infos[0].getMessage()


async def test_timer_drives_the_check_and_cancel_stops_it(hass, mock_charger, caplog):
    """The interval timer calls the check, and the returned callable stops it."""
    monitor = ChargerConnectionMonitor(hass, "entry-1", mock_charger)
    cancel = monitor.async_start()
    assert callable(cancel)

    with caplog.at_level(logging.INFO, logger="custom_components.wattpilot.availability"):
        mock_charger.connected = False
        async_fire_time_changed(hass, dt_util.utcnow() + AVAILABILITY_SCAN_INTERVAL + timedelta(seconds=1))
        await hass.async_block_till_done()
        assert [r for r in caplog.records if r.levelno == logging.WARNING]

        # After cancelling, later ticks must not reach the monitor any more.
        cancel()
        caplog.clear()
        mock_charger.connected = True
        async_fire_time_changed(hass, dt_util.utcnow() + 2 * AVAILABILITY_SCAN_INTERVAL + timedelta(seconds=1))
        await hass.async_block_till_done()
        assert caplog.records == []
