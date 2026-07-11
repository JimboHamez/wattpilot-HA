"""Unit tests for the pure helper logic in ``utils.py``.

Focuses on the two behaviours worth pinning down without a live charger:
value type-coercion in ``async_SetChargerProp`` and safe reads in
``GetChargerProp``. The module is imported lazily because importing it pulls in
Home Assistant and the vendored ``wattpilot`` library (which needs
``websocket-client``); if those are absent the whole module is skipped.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# Importing utils triggers Home Assistant imports and the dynamic load of the
# vendored wattpilot library (needs websocket-client). Skip the whole module
# cleanly if any of that is unavailable.
try:
    from custom_components.wattpilot import utils
except ImportError as exc:
    pytest.skip(f"integration import unavailable: {exc}", allow_module_level=True)


# --- async_SetChargerProp: type coercion -------------------------------------


async def test_numeric_string_coerced_to_int(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, "amp", "16") is True
    assert mock_charger.sent[-1] == ("amp", 16)
    assert isinstance(mock_charger.sent[-1][1], int)


async def test_boolean_like_string_coerced_to_bool(mock_charger):
    await utils.async_SetChargerProp(mock_charger, "cae", "true")
    assert mock_charger.sent[-1] == ("cae", True)
    assert isinstance(mock_charger.sent[-1][1], bool)


async def test_native_bool_coerced_to_bool(mock_charger):
    await utils.async_SetChargerProp(mock_charger, "cae", False)
    assert mock_charger.sent[-1] == ("cae", False)
    assert isinstance(mock_charger.sent[-1][1], bool)


async def test_force_type_str_keeps_numeric_as_string(mock_charger):
    await utils.async_SetChargerProp(mock_charger, "amp", 16, force_type="str")
    assert mock_charger.sent[-1] == ("amp", "16")
    assert isinstance(mock_charger.sent[-1][1], str)


async def test_force_type_float(mock_charger):
    await utils.async_SetChargerProp(mock_charger, "fte", "1.5", force_type="float")
    assert mock_charger.sent[-1] == ("fte", 1.5)
    assert isinstance(mock_charger.sent[-1][1], float)


async def test_namespace_value_sent_as_dict(mock_charger):
    mock_charger.allProps["cll"] = SimpleNamespace(requestedCurrent=16)
    await utils.async_SetChargerProp(mock_charger, "cll", SimpleNamespace(requestedCurrent=16))
    assert mock_charger.sent[-1] == ("cll", {"requestedCurrent": 16})


# --- async_SetChargerProp: guard rails ---------------------------------------


async def test_unknown_property_without_force_is_rejected(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, "does_not_exist", 1) is False
    assert mock_charger.sent == []


async def test_unknown_property_with_force_is_written(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, "does_not_exist", 1, force=True) is True
    assert mock_charger.sent[-1] == ("does_not_exist", 1)


async def test_none_value_is_rejected(mock_charger):
    assert await utils.async_SetChargerProp(mock_charger, "amp", None) is False
    assert mock_charger.sent == []


# --- GetChargerProp: safe reads ----------------------------------------------


def test_get_existing_property(mock_charger):
    assert utils.GetChargerProp(mock_charger, "amp") == 6


def test_get_missing_property_returns_default(mock_charger):
    assert utils.GetChargerProp(mock_charger, "nope", default="fallback") == "fallback"


def test_get_none_property_returns_default(make_charger):
    charger = make_charger(props={"x": None})
    assert utils.GetChargerProp(charger, "x", default=42) == 42


def test_get_prop_on_object_without_allprops_returns_default():
    assert utils.GetChargerProp(object(), "amp", default="d") == "d"
