"""Sanity checks on integration constants and manifest consistency.

Lightweight: reads const.py by import and manifest.json as data. const.py has
no Home Assistant imports, so this runs without the HA stack.
"""

from __future__ import annotations

import importlib.util
import json
import os

from .conftest import COMPONENT_DIR


def _const():
    """Load const.py in isolation.

    Importing it as ``custom_components.wattpilot.const`` would first execute the
    package ``__init__.py`` (which imports the full HA + vendored-library chain).
    const.py has no such dependencies, so load it directly from its file path.
    """
    path = os.path.join(COMPONENT_DIR, "const.py")
    spec = importlib.util.spec_from_file_location("_wattpilot_const", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supported_platforms_have_yaml_catalogs():
    for platform in _const().SUPPORTED_PLATFORMS:
        # 'diagnostics' has no YAML catalog; every other platform must.
        if platform == "diagnostics":
            continue
        assert os.path.isfile(
            os.path.join(COMPONENT_DIR, f"{platform}.yaml")
        ), f"platform '{platform}' has no {platform}.yaml"


def test_manifest_domain_matches_const():
    with open(os.path.join(COMPONENT_DIR, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["domain"] == _const().DOMAIN
    assert manifest["version"], "manifest.json must declare a non-empty version (HACS requirement)"
    assert manifest["codeowners"], "manifest.json must declare codeowners (HACS requirement)"


def test_event_props_are_strings():
    const = _const()
    assert isinstance(const.EVENT_PROPS, list)
    assert all(isinstance(p, str) for p in const.EVENT_PROPS)
    assert const.EVENT_PROPS_ID.startswith(const.DOMAIN)
