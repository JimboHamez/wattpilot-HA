"""Validate the data-driven entity YAML catalogs.

Entities in this integration are declared in per-platform YAML files rather
than in Python (see CLAUDE.md -> "Data-driven entities"). These tests guard the
structural contract every entity definition must satisfy, so a malformed
catalog fails here rather than silently dropping entities at runtime.

Pure ``pyyaml`` — no Home Assistant import — so they run without the HA stack.
"""

from __future__ import annotations

import glob
import os

import pytest
import yaml

from .conftest import COMPONENT_DIR

# Platforms whose YAML top-level key must match the file name.
PLATFORMS = ["sensor", "switch", "select", "number", "button", "update"]
VALID_SOURCES = {"property", "attribute", "namespacelist"}


def _load(platform: str) -> list[dict]:
    path = os.path.join(COMPONENT_DIR, f"{platform}.yaml")
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert platform in data, f"{platform}.yaml missing top-level '{platform}' key"
    return data[platform] or []


def test_all_platform_yaml_files_exist():
    for platform in PLATFORMS:
        assert os.path.isfile(os.path.join(COMPONENT_DIR, f"{platform}.yaml")), f"missing {platform}.yaml"


def test_every_yaml_file_parses():
    for path in glob.glob(os.path.join(COMPONENT_DIR, "*.yaml")):
        with open(path, encoding="utf-8") as handle:
            yaml.safe_load(handle)  # raises on invalid YAML


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entities_have_id(platform):
    for entity in _load(platform):
        assert entity.get("id"), f"{platform} entity without id: {entity}"


def test_sensor_entities_declare_valid_source():
    # sensor.py requires an explicit, valid 'source' per entity.
    for entity in _load("sensor"):
        source = entity.get("source")
        assert source in VALID_SOURCES, f"sensor '{entity.get('id')}' has bad source: {source}"


def test_namespacelist_entities_specify_indexes():
    # A namespacelist source needs namespace_id + value_id to resolve a state.
    for platform in PLATFORMS:
        for entity in _load(platform):
            if entity.get("source") == "namespacelist":
                assert "namespace_id" in entity, f"{entity.get('id')} missing namespace_id"
                assert "value_id" in entity, f"{entity.get('id')} missing value_id"


def test_unique_ids_are_unique_per_platform():
    # entities.py builds unique_id from uid|id; collisions would clobber entities.
    # Definitions sharing a uid are only legal when a gate (variant/connection/
    # firmware) makes them mutually exclusive at runtime — e.g. the 11kW vs 22kW
    # 'amp' number. Key uniqueness by (uid, gates) to allow those pairs.
    for platform in PLATFORMS:
        seen: set[tuple] = set()
        for entity in _load(platform):
            uid = entity.get("uid", entity.get("id"))
            key = (
                uid,
                entity.get("variant"),
                entity.get("connection"),
                entity.get("firmware"),
            )
            assert key not in seen, f"duplicate ungated uid '{uid}' in {platform}.yaml"
            seen.add(key)


def test_firmware_gate_uses_known_operator():
    valid_prefixes = (">=", "<=", "==", ">", "<")
    for platform in PLATFORMS:
        for entity in _load(platform):
            fw = entity.get("firmware")
            if fw is not None:
                assert str(fw).startswith(valid_prefixes), f"{entity.get('id')} bad firmware gate: {fw}"
