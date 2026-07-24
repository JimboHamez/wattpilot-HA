"""Tests for the shared platform setup skeleton.

Every platform's ``async_setup_entry`` follows the same shape: read the YAML
catalog, pull the charger out of the entry runtime data, build one entity per
definition. These tests drive that skeleton — including the failure branches,
which log and return rather than raising — for all six platforms at once.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot import button, number, select, sensor, switch, update
from custom_components.wattpilot.const import CONF_CHARGER, DOMAIN

PLATFORMS = [button, number, select, sensor, switch, update]
PLATFORM_IDS = [module.platform for module in PLATFORMS]

CHARGER_PROPS = {
    "amp": 6,
    "car": 1,
    "cae": False,
    "fap": False,
    "fup": False,
    "frc": 0,
    "fte": 0,
    "lmo": 3,
    "nrg": [0] * 16,
    "onv": "38.5",
    "sse": "SN",
    "tma": 5.0,
    "typ": "model",
    "ust": 0,
    "var": 11,
}


def _entry(hass, runtime_data):
    """Return a config entry carrying the given runtime data."""
    entry = MockConfigEntry(domain=DOMAIN, data={"friendly_name": "WB"})
    entry.add_to_hass(hass)
    entry.runtime_data = runtime_data
    return entry


@pytest.mark.parametrize("module", PLATFORMS, ids=PLATFORM_IDS)
async def test_setup_adds_entities(hass, make_charger, module):
    """Each platform builds entities from its own YAML catalog."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props=dict(CHARGER_PROPS), serial="SN", name="WB")})
    added = MagicMock()

    await module.async_setup_entry(hass, entry, added)

    assert added.called, f"{module.platform} added no entities"
    assert added.call_args.args[0], f"{module.platform} added an empty entity list"


@pytest.mark.parametrize("module", PLATFORMS, ids=PLATFORM_IDS)
async def test_setup_without_a_catalog_is_logged(hass, make_charger, module, caplog):
    """An unreadable YAML catalog aborts that platform without raising."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props=dict(CHARGER_PROPS))})
    added = MagicMock()

    with (
        caplog.at_level(logging.ERROR, logger=f"custom_components.wattpilot.{module.platform}"),
        patch.object(module.aiofiles, "open", side_effect=OSError("no such file")),
    ):
        await module.async_setup_entry(hass, entry, added)

    added.assert_not_called()
    assert any("Reading static yaml configuration failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("module", PLATFORMS, ids=PLATFORM_IDS)
async def test_setup_without_a_charger_is_logged(hass, module, caplog):
    """A runtime data store with no charger aborts that platform."""
    entry = _entry(hass, {})
    added = MagicMock()

    with caplog.at_level(logging.ERROR, logger=f"custom_components.wattpilot.{module.platform}"):
        await module.async_setup_entry(hass, entry, added)

    added.assert_not_called()
    assert any("Getting charger instance from data store failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("module", PLATFORMS, ids=PLATFORM_IDS)
async def test_setup_skips_definitions_without_an_id(hass, make_charger, module, caplog):
    """A catalog entry with no id is reported and skipped."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props=dict(CHARGER_PROPS))})
    added = MagicMock()

    with (
        caplog.at_level(logging.ERROR, logger=f"custom_components.wattpilot.{module.platform}"),
        patch.object(module.yaml, "safe_load", return_value={module.platform: [{"id": None}]}),
    ):
        await module.async_setup_entry(hass, entry, added)

    added.assert_not_called()
    assert any("no id" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("module", PLATFORMS, ids=PLATFORM_IDS)
async def test_setup_adds_nothing_when_the_catalog_is_empty(hass, make_charger, module):
    """An empty catalog is not an error, it just adds no entities."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props=dict(CHARGER_PROPS))})
    added = MagicMock()

    with patch.object(module.yaml, "safe_load", return_value={module.platform: []}):
        await module.async_setup_entry(hass, entry, added)

    added.assert_not_called()


@pytest.mark.parametrize("module", PLATFORMS, ids=PLATFORM_IDS)
async def test_setup_reports_a_failing_entity_definition(hass, make_charger, module, caplog):
    """A definition that cannot be turned into an entity is logged."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props=dict(CHARGER_PROPS))})
    added = MagicMock()

    # A non-dict definition breaks on the first attribute access inside the loop.
    with (
        caplog.at_level(logging.ERROR, logger=f"custom_components.wattpilot.{module.platform}"),
        patch.object(module.yaml, "safe_load", return_value={module.platform: ["not-a-definition"]}),
    ):
        await module.async_setup_entry(hass, entry, added)

    added.assert_not_called()
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.parametrize("missing", ["id_installed", "id_trigger"])
async def test_update_setup_requires_its_extra_ids(hass, make_charger, missing, caplog):
    """The update platform needs the installed-version and trigger property ids."""
    entry = _entry(hass, {CONF_CHARGER: make_charger(props=dict(CHARGER_PROPS))})
    added = MagicMock()
    definition = {"id": "onv", "id_installed": "fwv", "id_trigger": "oct"}
    definition[missing] = None

    with (
        caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.update"),
        patch.object(update.yaml, "safe_load", return_value={"update": [definition]}),
    ):
        await update.async_setup_entry(hass, entry, added)

    added.assert_not_called()
    assert any(f"no {missing}" in r.getMessage() for r in caplog.records)
