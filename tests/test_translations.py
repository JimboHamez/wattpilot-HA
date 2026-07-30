"""Consistency checks across strings.json and the translation files.

``strings.json`` is the English source and ``translations/en.json`` is what Home
Assistant actually serves, so the two must agree; every other language must
carry the same key set. Nothing at runtime notices when they drift - the two
enum blocks that were humanised in en.json while strings.json kept the raw
charger codes went unnoticed until an audit - so assert it here.
"""

from __future__ import annotations

import glob
import json
import os

import pytest
import yaml

pytest.importorskip("homeassistant")
from homeassistant.util import slugify

from .conftest import COMPONENT_DIR

PLATFORMS = ["button", "number", "select", "sensor", "switch", "update"]
TRANSLATIONS_DIR = os.path.join(COMPONENT_DIR, "translations")


def _load(path: str) -> dict:
    """Return the parsed JSON at a path."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    """Return the nested strings as one dotted-key mapping."""
    flat: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _languages() -> list[str]:
    """Return the translation files shipped alongside strings.json."""
    return sorted(glob.glob(os.path.join(TRANSLATIONS_DIR, "*.json")))


def test_english_translation_matches_the_source_strings():
    """translations/en.json is the English source verbatim."""
    source = _flatten(_load(os.path.join(COMPONENT_DIR, "strings.json")))
    english = _flatten(_load(os.path.join(TRANSLATIONS_DIR, "en.json")))

    differing = {key: (source[key], english.get(key)) for key in source if source[key] != english.get(key)}

    assert not differing, f"strings.json and translations/en.json disagree: {sorted(differing)}"


@pytest.mark.parametrize("path", _languages(), ids=lambda path: os.path.basename(path))
def test_every_language_carries_the_same_keys(path):
    """A translation may differ in wording, never in which keys it defines."""
    source = set(_flatten(_load(os.path.join(COMPONENT_DIR, "strings.json"))))
    translated = set(_flatten(_load(path)))

    assert not source - translated, f"{os.path.basename(path)} is missing keys: {sorted(source - translated)}"
    assert not translated - source, f"{os.path.basename(path)} has unknown keys: {sorted(translated - source)}"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_catalog_entity_has_a_translated_name(platform):
    """Entities take their visible name from the translations, so all need one."""
    with open(os.path.join(COMPONENT_DIR, f"{platform}.yaml"), encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    catalog = {slugify(str(item.get("uid", item.get("id")))) for item in cfg[platform] or []}
    named = set(_load(os.path.join(COMPONENT_DIR, "strings.json"))["entity"].get(platform, {}))

    assert not catalog - named, f"{platform}.yaml entities without a name in strings.json: {sorted(catalog - named)}"
    assert not named - catalog, (
        f"strings.json names entities {platform}.yaml does not define: {sorted(named - catalog)}"
    )
