"""Shared pytest fixtures and helpers for the Wattpilot integration tests.

This conftest deliberately avoids importing Home Assistant or the integration
at module load time so that the lightweight, dependency-free tests (e.g. the
YAML catalog checks) can run even when the full HA test stack is not installed.
Tests that need the integration import it lazily via ``importorskip``.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

# Make the repository root importable so ``custom_components.wattpilot`` resolves
# (``custom_components`` is a PEP-420 namespace package with no __init__.py).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

COMPONENT_DIR = os.path.join(REPO_ROOT, "custom_components", "wattpilot")


class MockCharger:
    """Minimal stand-in for the wattpilot_api ``Wattpilot`` charger object.

    Mirrors only the surface the integration touches: an ``all_properties`` dict
    of live properties, plus ``connected`` / ``properties_initialized`` flags,
    and an async ``set_property`` method that records the (identifier, value)
    pairs written to it so tests can assert on type coercion.
    """

    def __init__(self, props: dict | None = None, **attrs) -> None:
        self.all_properties: dict = dict(props or {})
        self.connected = True
        self.properties_initialized = True
        self.name = attrs.pop("name", "TestCharger")
        self.serial = attrs.pop("serial", "123456")
        self.sent: list[tuple[str, object]] = []
        self._property_callbacks: list = []
        for key, value in attrs.items():
            setattr(self, key, value)

    async def set_property(self, identifier: str, value) -> None:
        """Record a property write instead of hitting the wire."""
        self.sent.append((identifier, value))
        self.all_properties[identifier] = value

    def on_property_change(self, callback):
        """Register a property-change callback; returns an unsubscribe callable."""
        self._property_callbacks.append(callback)
        return lambda: self._property_callbacks.remove(callback)


@pytest.fixture
def mock_charger() -> MockCharger:
    """A charger pre-populated with a representative slice of properties."""
    return MockCharger(
        props={
            "amp": 6,
            "frc": 0,
            "nrg": [230, 230, 230, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            "cae": False,
            "fte": 0,
            "acs": 0,
        }
    )


@pytest.fixture
def make_charger():
    """Factory fixture to build a ``MockCharger`` with custom props/attrs."""

    def _factory(props: dict | None = None, **attrs) -> MockCharger:
        return MockCharger(props=props, **attrs)

    return _factory
