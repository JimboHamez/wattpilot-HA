"""Manual write test against a real charger, using the async ``wattpilot-api`` library.

Historic scaffolding, kept as the reference for raw property values and for the
type-coercion order that ``utils.py::async_SetChargerProp`` mirrors. Ported off the
synchronous ``wattpilot`` module that this integration stopped using in 0.5.0.

WARNING: unlike ``tests/live_probe.py`` (read-only) and ``tests/live_e2e.py`` (one
idempotent no-op write), this script really does change charger settings — it toggles
battery boost and overwrites access-control, OCPP and next-trip related properties. Run
it only against a charger you are happy to reconfigure.

Charger address and password are read from the gitignored ``.wp_test.json``; see
``.wp_test.example.json``. Never hardcode or commit credentials here.

Usage:
    .venv-test/bin/python set_values_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from typing import Any

from wattpilot_api import Wattpilot

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = os.path.join(REPO_ROOT, ".wp_test.json")

# Properties printed before and after the write pass. 'ebe' (boost active/inactive) doubles
# as the simple check for whether changes were applied at all.
PROPS = ["ebe", "acs", "ocppcs", "npd", "ebv", "modelStatus", "cll"]


def coerce_value(value: Any, force_type: str | None = None) -> Any:
    """Coerce a value the way the integration does before sending it to the charger.

    The order (explicit force_type -> bool -> namespace -> int -> float -> str) is the
    reference ``utils.py::async_SetChargerProp`` follows.
    """
    if str(value).lower() in ["false", "true"] or force_type == "bool":
        return json.loads(str(value).lower())
    if type(value) is types.SimpleNamespace:
        return value.__dict__
    if force_type == "str":
        return str(value)
    if str(value).isnumeric() or force_type == "int":
        return int(value)
    if str(value).isdecimal() or force_type == "float":
        return float(value)
    return str(value)


async def send_value(charger: Wattpilot, identifier: str, value: Any, force_type: str | None = None) -> None:
    """Coerce a value and write it to the charger."""
    await charger.set_property(identifier, coerce_value(value, force_type))


def print_value(charger: Wattpilot, identifier: str) -> None:
    """Print the charger's current value for a property."""
    print(identifier, " = ", charger.all_properties.get(identifier))


async def main() -> None:
    """Connect, dump the properties, write new values, and dump them again."""
    with open(SECRETS_FILE) as f:
        cfg = json.load(f)
    cloud = cfg.get("connection", "local") == "cloud"
    host = cfg["serial"] if cloud else cfg["ip"]
    timeout = int(cfg.get("timeout", 30))

    charger = Wattpilot(
        host=host,
        password=cfg["password"],
        serial=cfg.get("serial") or host,
        cloud=cloud,
        connect_timeout=timeout,
        init_timeout=timeout,
    )
    # connect() waits for authentication and property initialisation, and raises on failure.
    await charger.connect()
    print("Charger connected")

    try:
        print("====== GET VALUES PRE =======")
        for prop in PROPS:
            print_value(charger, prop)

        print("======= SET VALUES =======")
        await send_value(charger, "ebe", not charger.all_properties["ebe"])
        await send_value(charger, "acs", 1)
        await send_value(charger, "ocppcs", 1)
        await send_value(charger, "npd", True)
        await send_value(charger, "ebv", True)
        # await send_value(charger, "modelStatus", 17)  # ReadOnly value.

        cll = charger.all_properties["cll"]
        # cll locked 11kW: namespace(accessControl=0, adapterCurrentLimit=16, cableCurrentLimit=32,
        #   currentLimitMax=16, requestedCurrent=16, temperatureCurrentLimit=32, unsymetryCurrentLimit=32)
        # cll unlocked 11kW: namespace(adapterCurrentLimit=16, cableCurrentLimit=32,
        #   currentLimitMax=16, requestedCurrent=16, temperatureCurrentLimit=32, unsymetryCurrentLimit=32)
        if hasattr(cll, "accessControl"):
            del cll.accessControl
        await send_value(charger, "cll", cll)

        await asyncio.sleep(3)

        print("====== GET VALUES POST =======")
        for prop in PROPS:
            print_value(charger, prop)
    finally:
        await charger.disconnect()


if __name__ == "__main__":
    if not os.path.exists(SECRETS_FILE):
        sys.exit(f"Missing {SECRETS_FILE} - copy .wp_test.example.json and fill in your charger.")
    asyncio.run(main())
