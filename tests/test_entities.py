"""Tests for the shared entity base class.

Covers safe indexing of 'namespacelist' sources: the backing property can be
missing or not a list on some firmware (e.g. 'cards' removed in go-e firmware
60.0, where GetChargerProp returns the int default_state), which previously
crashed with "'int' object is not subscriptable".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

try:
    from custom_components.wattpilot.entities import ChargerPlatformEntity
except ImportError as exc:
    pytest.skip(f"integration import unavailable: {exc}", allow_module_level=True)


def _index(value, namespace_id=0):
    stub = SimpleNamespace(_namespace_id=namespace_id)
    return ChargerPlatformEntity._index_namespace(stub, value)


def test_int_default_returns_none():
    # The reported crash: property missing -> default_state -1 (int) indexed.
    assert _index(-1) is None


def test_none_returns_none():
    assert _index(None) is None


def test_list_in_range_returns_item():
    assert _index(["a", "b", "c"], namespace_id=2) == "c"


def test_list_out_of_range_returns_none():
    assert _index(["a"], namespace_id=3) is None


def test_namespace_item_returned():
    ns = SimpleNamespace(energy=42)
    assert _index([ns], namespace_id=0) is ns
