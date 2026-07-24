"""Consistency checks between icons.json and the entity catalogs.

Icons live in icons.json rather than on the entities (quality-scale rule
``icon-translations``), keyed by the same slugified uid/id that becomes an
entity's ``_attr_translation_key``. Nothing at runtime notices when the two
drift apart, so assert the mapping here.
"""

from __future__ import annotations

import json
import os


import pytest
import yaml

pytest.importorskip("homeassistant")
from homeassistant.util import slugify

from .conftest import COMPONENT_DIR

PLATFORMS = ["button", "number", "select", "sensor", "switch", "update"]


def _icons() -> dict:
    """Return the parsed icons.json."""
    with open(os.path.join(COMPONENT_DIR, "icons.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _catalog_keys(platform: str) -> set[str]:
    """Return the translation keys of every entity defined for a platform."""
    with open(os.path.join(COMPONENT_DIR, f"{platform}.yaml"), encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    return {slugify(str(item.get("uid", item.get("id")))) for item in cfg[platform] or []}


def test_icons_json_has_the_expected_shape():
    """icons.json holds one entity section per platform."""
    icons = _icons()
    assert set(icons) == {"entity"}
    assert set(icons["entity"]) <= set(PLATFORMS)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_icon_keys_exist_in_the_catalog(platform):
    """Every icon key belongs to an entity that the catalog actually defines."""
    orphans = set(_icons()["entity"].get(platform, {})) - _catalog_keys(platform)
    assert not orphans, f"{platform}.yaml defines no entity for icons.json keys: {sorted(orphans)}"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_icons_are_mdi_defaults(platform):
    """Each entry is a default icon from the Material Design Icons set."""
    for key, value in _icons()["entity"].get(platform, {}).items():
        assert "default" in value, f"{platform}.{key} has no default icon"
        assert value["default"].startswith("mdi:"), f"{platform}.{key} is not an mdi icon: {value['default']}"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_catalogs_no_longer_carry_icons(platform):
    """The catalogs must not reintroduce an 'icon' field; icons.json owns them."""
    with open(os.path.join(COMPONENT_DIR, f"{platform}.yaml"), encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    strays = [item.get("uid", item.get("id")) for item in cfg[platform] or [] if "icon" in item]
    assert not strays, f"{platform}.yaml sets 'icon' on {strays}; move it to icons.json"
