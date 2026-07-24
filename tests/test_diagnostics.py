"""Tests for the redacted diagnostics download."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import CONF_CHARGER, CONF_CONNECTION, CONF_LOCAL, DOMAIN
from custom_components.wattpilot.diagnostics import async_get_config_entry_diagnostics

ENTRY_DATA = {
    CONF_CONNECTION: CONF_LOCAL,
    CONF_IP_ADDRESS: "1.2.3.4",
    CONF_PASSWORD: "hunter2",
    "friendly_name": "WB",
}


def _entry(hass, runtime_data):
    """Return a config entry carrying the given runtime data."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    entry.runtime_data = runtime_data
    return entry


async def test_diagnostics_redacts_credentials_and_secret_properties(hass, make_charger):
    """The download carries config and properties, with the secrets removed."""
    charger = make_charger(props={"amp": 6, "cak": "cloud-key", "wifis": ["ssid"], "sse": "SN"})
    entry = _entry(hass, {CONF_CHARGER: charger})

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["config"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["config"]["data"][CONF_IP_ADDRESS] == "**REDACTED**"
    assert diag["charger_properties"]["amp"] == 6
    assert diag["charger_properties"]["cak"] == "**REDACTED**"
    assert diag["charger_properties"]["wifis"] == "**REDACTED**"


async def test_diagnostics_reports_dependency_versions(hass, make_charger):
    """Library versions are included to make bug reports actionable."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props={"amp": 6})})

    diag = await async_get_config_entry_diagnostics(hass, entry)

    for key in ("wattpilot_module", "pyyaml_module", "importlib_metadata_module", "aiofiles_module", "packaging"):
        assert diag[key], f"{key} is missing from the diagnostics"
    assert diag["wattpilot_file"].endswith(".py")


async def test_diagnostics_without_a_charger_returns_empty(hass):
    """A config entry with no charger in its runtime data yields nothing."""
    entry = _entry(hass, {})

    assert await async_get_config_entry_diagnostics(hass, entry) == {}


async def test_diagnostics_without_a_usable_config_returns_empty(hass, make_charger):
    """A config section that cannot be built aborts the download."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props={"amp": 6})})

    with patch("custom_components.wattpilot.diagnostics.async_redact_data", side_effect=RuntimeError("boom")):
        assert await async_get_config_entry_diagnostics(hass, entry) == {}


async def test_diagnostics_keeps_earlier_sections_when_versions_fail(hass, make_charger):
    """Unreadable dependency versions still leave config and properties in place."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props={"amp": 6})})

    with patch("custom_components.wattpilot.diagnostics.version", side_effect=RuntimeError("boom")):
        diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["charger_properties"]["amp"] == 6
    assert "wattpilot_module" not in diag


async def test_diagnostics_keeps_the_config_when_properties_fail(hass):
    """An unreadable property dict still leaves the config section usable."""

    class _BrokenCharger:
        @property
        def all_properties(self):
            raise RuntimeError("boom")

    entry = _entry(hass, {CONF_CHARGER: _BrokenCharger()})

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert "config" in diag
    assert "charger_properties" not in diag
