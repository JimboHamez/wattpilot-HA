"""Regression tests for the select platform's option handling.

The `lmo` (charging mode) and `ust` (cable unlock) selects used to source their
options from `charger.lmoValues` / `charger.ustValues` attributes on the old
vendored library. `wattpilot-api` does not expose those, which left the
dropdowns empty; the options are now static dicts in select.yaml.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import yaml

from custom_components.wattpilot import select as _select_mod
from custom_components.wattpilot.select import ChargerSelect

_SELECT_YAML = os.path.join(os.path.dirname(_select_mod.__file__), "select.yaml")
_SELECTS = {c["id"]: c for c in yaml.safe_load(open(_SELECT_YAML))["select"]}


def _make_select(cid: str, charger) -> ChargerSelect:
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {"friendly_name": "WB"}
    cfg = dict(_SELECTS[cid])
    cfg["source"] = "property"
    return ChargerSelect(hass, entry, cfg, charger)


async def test_lmo_select_has_options_and_maps_state(make_charger):
    """The charging-mode select exposes translated slug options and maps its value."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11}, name="WB")
    entity = _make_select("lmo", charger)
    assert entity._attr_options == ["default", "eco", "next_trip"]
    # The live charger reports lmo=3 -> "Default".
    assert await entity._async_update_validate_platform_state(3) == "default"


async def test_ust_select_has_options_and_maps_state(make_charger):
    """The cable-unlock select exposes translated slug options and maps its value."""
    charger = make_charger(props={"ust": 0, "typ": "m", "var": 11}, name="WB")
    entity = _make_select("ust", charger)
    assert entity._attr_options == ["normal", "autounlock", "alwayslock"]
    assert await entity._async_update_validate_platform_state(0) == "normal"


async def test_select_round_trips_slug_to_raw_key(make_charger):
    """Selecting a slug option writes the raw charger key back."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11}, name="WB")
    entity = _make_select("lmo", charger)
    await entity.async_select_option("next_trip")
    assert charger.sent[-1] == ("lmo", 5)
