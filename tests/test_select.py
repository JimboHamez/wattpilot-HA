"""Regression tests for the select platform's option handling.

The `lmo` (charging mode) and `ust` (cable unlock) selects used to source their
options from `charger.lmoValues` / `charger.ustValues` attributes on the old
vendored library. `wattpilot-api` does not expose those, which left the
dropdowns empty; the options are now static dicts in select.yaml.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import yaml

from custom_components.wattpilot import select as _select_mod
from custom_components.wattpilot.select import ChargerSelect
from custom_components.wattpilot.utils import property_update_signal

_SELECT_YAML = os.path.join(os.path.dirname(_select_mod.__file__), "select.yaml")
with open(_SELECT_YAML, encoding="utf-8") as _handle:
    _CATALOG = yaml.safe_load(_handle)["select"]
_SELECTS = {c["id"]: c for c in _CATALOG if "uid" not in c}
_SELECTS_BY_UID = {c.get("uid", c["id"]): c for c in _CATALOG}


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


# --- charging-current preset (options from the 'clp' list property) ------------
#
# The app's charging-speed slider only has stops at the 'clp' presets, so a
# current set from the 1 A number entity leaves it stuck. This select writes the
# same 'amp' but only ever a preset, and its options follow 'clp' at runtime.


def _preset_select(charger, **overrides) -> ChargerSelect:
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {"friendly_name": "WB"}
    cfg = dict(_SELECTS_BY_UID["amp_preset"])
    cfg["source"] = "property"
    cfg.update(overrides)
    return ChargerSelect(hass, entry, cfg, charger)


async def test_preset_select_builds_its_options_from_clp(make_charger):
    """Every preset the charger lists is an option, labelled with its value."""
    charger = make_charger(props={"amp": 16, "clp": [10, 16, 20, 24, 32], "typ": "m", "var": 11}, name="WB")
    entity = _preset_select(charger)

    assert entity._init_failed is False
    assert entity._attr_options == ["10", "16", "20", "24", "32"]
    assert entity._attr_unique_id == "123456-amp_preset"
    assert await entity._async_update_validate_platform_state(16) == "16"


async def test_preset_select_labels_integral_floats_as_integers(make_charger):
    """A charger reporting 16.0 still offers '16', while a real fraction is kept."""
    charger = make_charger(props={"amp": 16, "clp": [10.0, 12.5, None], "typ": "m", "var": 11}, name="WB")
    entity = _preset_select(charger)

    assert entity._attr_options == ["10", "12.5"]


async def test_preset_select_is_skipped_when_the_charger_has_no_presets(make_charger):
    """No 'clp' list (or a non-list value) means the entity cannot be offered."""
    charger = make_charger(props={"amp": 16, "typ": "m", "var": 11}, name="WB")
    assert _preset_select(charger)._init_failed is True

    charger = make_charger(props={"amp": 16, "clp": 16, "typ": "m", "var": 11}, name="WB")
    assert _preset_select(charger)._init_failed is True


async def test_preset_select_writes_the_preset_as_an_int(make_charger):
    """Choosing a preset writes 'amp' with the raw integer value."""
    charger = make_charger(props={"amp": 16, "clp": [10, 16, 20], "typ": "m", "var": 11}, name="WB")
    entity = _preset_select(charger)

    await entity.async_select_option("20")

    assert charger.sent[-1] == ("amp", 20)
    assert isinstance(charger.sent[-1][1], int)


async def test_preset_select_clears_the_selection_for_a_value_between_presets(make_charger, caplog):
    """13 A set from the number entity is no preset: the select shows nothing, and no error is logged."""
    charger = make_charger(props={"amp": 16, "clp": [10, 16, 20], "typ": "m", "var": 11}, name="WB")
    entity = _preset_select(charger)
    entity.entity_id = "select.wb_amp_preset"
    entity.async_write_ha_state = MagicMock()
    entity._attr_current_option = "16"

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.select"):
        assert await entity._async_update_validate_platform_state(13) is None

    assert entity._attr_current_option is None
    entity.async_write_ha_state.assert_called_once()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_preset_select_does_not_write_state_before_it_is_added(make_charger):
    """Clearing the selection before the entity has an entity_id must not touch hass."""
    charger = make_charger(props={"amp": 13, "clp": [10, 16, 20], "typ": "m", "var": 11}, name="WB")
    entity = _preset_select(charger)
    entity.async_write_ha_state = MagicMock()

    assert await entity._async_update_validate_platform_state(13) is None

    entity.async_write_ha_state.assert_not_called()


async def test_preset_select_follows_a_pushed_clp_change(make_charger):
    """A new preset list from the charger replaces the options and re-checks the state."""
    charger = make_charger(props={"amp": 16, "clp": [10, 16, 20], "typ": "m", "var": 11}, name="WB")
    entity = _preset_select(charger)
    entity.async_local_poll = MagicMock(return_value=None)

    entity._handle_options_update([6, 8, 16])

    assert entity._attr_options == ["6", "8", "16"]
    assert await entity._async_update_validate_platform_state(8) == "8"
    entity.hass.async_create_task.assert_called_once()


async def test_preset_select_subscribes_to_clp_on_add(make_charger):
    """Adding the entity subscribes to the 'clp' signal as well as the 'amp' one."""
    charger = make_charger(props={"amp": 16, "clp": [10, 16, 20], "typ": "m", "var": 11}, name="WB")
    entity = _preset_select(charger)
    entity.entity_id = "select.wb_amp_preset"
    entity.async_on_remove = MagicMock()

    with (
        patch("custom_components.wattpilot.select.async_dispatcher_connect") as connect_select,
        patch("custom_components.wattpilot.entities.async_dispatcher_connect") as connect_base,
    ):
        await entity.async_added_to_hass()

    connect_base.assert_called_once()
    assert connect_base.call_args.args[1] == property_update_signal("e1", "amp")
    connect_select.assert_called_once()
    assert connect_select.call_args.args[1] == property_update_signal("e1", "clp")


async def test_static_select_does_not_subscribe_to_an_options_property(make_charger):
    """A select with a static option dict has nothing extra to subscribe to."""
    charger = make_charger(props={"lmo": 3, "typ": "m", "var": 11}, name="WB")
    entity = _make_select("lmo", charger)
    entity.entity_id = "select.wb_lmo"
    entity.async_on_remove = MagicMock()

    with (
        patch("custom_components.wattpilot.select.async_dispatcher_connect") as connect_select,
        patch("custom_components.wattpilot.entities.async_dispatcher_connect"),
    ):
        await entity.async_added_to_hass()

    connect_select.assert_not_called()
