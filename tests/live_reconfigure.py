"""Live check of the config flow's reconfiguration logic against a real charger.

Exercises the half of ``async_step_reconfigure`` that mocks cannot verify: whether a
real charger actually answers with the serial (``sse``) the identity guard depends on,
and how ``_is_same_charger`` / ``_reconfigured_unique_id`` then behave for each shape of
config entry. The Home Assistant form machinery around it is covered by
``tests/test_config_flow.py``.

READ-ONLY: it connects, reads properties and disconnects. Nothing is written to the
charger and no config entry is touched — there is no Home Assistant instance involved.

Usage:
    .venv/bin/python tests/live_reconfigure.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from custom_components.wattpilot.config_flow import ConfigFlowHandler  # noqa: E402
from custom_components.wattpilot.const import (  # noqa: E402
    CONF_CLOUD,
    CONF_CONNECTION,
    CONF_LOCAL,
    CONF_SERIAL,
)

SECRETS_FILE = os.path.join(REPO_ROOT, ".wp_test.json")


class FakeEntry:
    """The two attributes of a ConfigEntry the identity helpers read."""

    def __init__(self, unique_id: str | None, data: dict) -> None:
        """Store the entry identity the helpers are given."""
        self.unique_id = unique_id
        self.data = data


def check(label: str, actual: object, expected: object) -> bool:
    """Print and return whether one expectation held."""
    ok = actual == expected
    print(f"   [{'PASS' if ok else 'FAIL'}] {label}: got {actual!r}, expected {expected!r}")
    return ok


async def main() -> int:
    """Run the live checks and return a process exit code."""
    with open(SECRETS_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    con = cfg.get("connection", "local")
    data = {
        CONF_CONNECTION: con,
        CONF_FRIENDLY_NAME: "live-reconfigure",
        CONF_PASSWORD: cfg["password"],
        CONF_TIMEOUT: int(cfg.get("timeout", 30)),
    }
    if con == CONF_CLOUD:
        data[CONF_SERIAL] = cfg["serial"]
    else:
        data[CONF_IP_ADDRESS] = cfg["ip"]

    flow = ConfigFlowHandler()

    print("1) _async_validate_charger against the real charger ...")
    error, serial = await flow._async_validate_charger(dict(data))
    if error is not None:
        print(f"   FAILED to validate: {error}")
        return 1
    print(f"   connected; charger reports sse={serial!r}")
    if not serial:
        print("   WARNING: no serial reported - the identity guard would accept any charger.")
        return 1

    results: list[bool] = []

    print("2) wrong credentials are still reported as an error ...")
    bad = {**data, CONF_PASSWORD: "definitely-not-the-password"}
    bad_error, bad_serial = await flow._async_validate_charger(bad)
    results.append(check("error key", bad_error, "invalid_auth"))
    results.append(check("serial withheld", bad_serial, None))

    print("3) _is_same_charger against the real serial ...")
    discovered = FakeEntry(serial, {CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: cfg["ip"], CONF_SERIAL: serial})
    manual = FakeEntry(cfg["ip"], {CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: cfg["ip"]})
    other = FakeEntry("00000000", {CONF_CONNECTION: CONF_LOCAL, CONF_SERIAL: "00000000"})
    results.append(check("same charger accepted", flow._is_same_charger(discovered, serial), True))
    results.append(check("different charger refused", flow._is_same_charger(other, serial), False))
    results.append(check("no stored serial cannot be checked", flow._is_same_charger(manual, serial), True))

    print("4) _reconfigured_unique_id for each entry shape ...")
    moved = {CONF_IP_ADDRESS: "192.168.0.44"}
    results.append(
        check("serial-keyed entry keeps its id", flow._reconfigured_unique_id(discovered, moved, serial), serial)
    )
    results.append(
        check("ip-keyed entry follows the address", flow._reconfigured_unique_id(manual, moved, serial), "192.168.0.44")
    )

    print("5) the serial the charger reports matches the configured one ...")
    configured = str(cfg.get("serial") or "")
    if configured:
        results.append(check("sse == .wp_test.json serial", serial, configured))
    else:
        print("   [SKIP] no serial in .wp_test.json to compare against")

    failed = results.count(False)
    print(f"\n{'ALL CHECKS PASSED' if not failed else f'{failed} CHECK(S) FAILED'} ({len(results)} checks)")
    return 1 if failed else 0


if __name__ == "__main__":
    if not os.path.exists(SECRETS_FILE):
        sys.exit(f"Missing {SECRETS_FILE} - copy .wp_test.example.json and fill in your charger.")
    sys.exit(asyncio.run(main()))
