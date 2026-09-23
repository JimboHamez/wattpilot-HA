"""Tests for the redacted diagnostics download."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import CONF_CONNECTION, CONF_LOCAL, DOMAIN
from custom_components.wattpilot.diagnostics import async_get_config_entry_diagnostics
from custom_components.wattpilot.models import WattpilotRuntimeData

ENTRY_DATA = {
    CONF_CONNECTION: CONF_LOCAL,
    CONF_IP_ADDRESS: "1.2.3.4",
    CONF_PASSWORD: "hunter2",
    "friendly_name": "WB",
}


def _entry(hass, charger):
    """Return a config entry whose runtime data holds the given charger, or none if it is None."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    if charger is not None:
        entry.runtime_data = WattpilotRuntimeData(charger=charger, params=entry.data)
    return entry


async def test_diagnostics_redacts_credentials_and_secret_properties(hass, make_charger):
    """The download carries config and properties, with the secrets removed."""
    charger = make_charger(props={"amp": 6, "cak": "cloud-key", "wifis": ["ssid"], "sse": "SN"})
    entry = _entry(hass, charger)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["config"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["config"]["data"][CONF_IP_ADDRESS] == "**REDACTED**"
    assert diag["charger_properties"]["amp"] == 6
    assert diag["charger_properties"]["cak"] == "**REDACTED**"
    assert diag["charger_properties"]["wifis"] == "**REDACTED**"


async def test_diagnostics_redacts_identifiers_outside_the_entry_data(hass, make_charger):
    """The IP a local entry is keyed by, its title and the charger's identity are all removed."""
    charger = make_charger(
        props={"amp": 6, "sse": "91111999", "wak": "ap-password", "facwak": "factory", "c0n": "Jim", "wss": "home"}
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1.2.3.4",
        title="1.2.3.4",
        data={CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "1.2.3.4", CONF_PASSWORD: "hunter2"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = WattpilotRuntimeData(charger=charger, params=entry.data)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert "1.2.3.4" not in str(diag)
    assert diag["config"]["unique_id"] == "**REDACTED**"
    assert diag["config"]["title"] == "**REDACTED**"
    for key in ("sse", "wak", "facwak", "c0n", "wss"):
        assert diag["charger_properties"][key] == "**REDACTED**", key
    assert diag["charger_properties"]["amp"] == 6


async def test_diagnostics_reports_dependency_versions(hass, make_charger):
    """Library versions are included to make bug reports actionable."""
    entry = _entry(hass, make_charger(props={"amp": 6}))

    diag = await async_get_config_entry_diagnostics(hass, entry)

    for key in ("wattpilot_module", "pyyaml_module", "aiofiles_module", "packaging"):
        assert diag[key], f"{key} is missing from the diagnostics"
    assert diag["wattpilot_file"].endswith(".py")


async def test_diagnostics_without_a_charger_returns_empty(hass):
    """A config entry that was never set up has no charger and yields nothing."""
    entry = _entry(hass, None)

    assert await async_get_config_entry_diagnostics(hass, entry) == {}


async def test_diagnostics_without_a_usable_config_returns_empty(hass, make_charger):
    """A config section that cannot be built aborts the download."""
    entry = _entry(hass, make_charger(props={"amp": 6}))

    with patch("custom_components.wattpilot.diagnostics.async_redact_data", side_effect=RuntimeError("boom")):
        assert await async_get_config_entry_diagnostics(hass, entry) == {}


async def test_diagnostics_keeps_earlier_sections_when_versions_fail(hass, make_charger):
    """Unreadable dependency versions still leave config and properties in place."""
    entry = _entry(hass, make_charger(props={"amp": 6}))

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

    entry = _entry(hass, _BrokenCharger())

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert "config" in diag
    assert "charger_properties" not in diag
