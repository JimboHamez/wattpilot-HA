"""Live end-to-end check of the integration's own utils against a real charger.

Exercises the MIGRATED integration code paths (not just the library):
connect, live property-push callback, GetChargerProp reads, and a SAFE no-op
write (sets ``amp`` back to its current value, so charging behaviour does not
change). Read-only apart from that single idempotent write.

Usage:
    .venv-test/bin/python tests/live_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from custom_components.wattpilot.const import (  # noqa: E402
    CONF_CONNECTION,
    CONF_LOCAL,
    CONF_CLOUD,
    CONF_SERIAL,
)
from custom_components.wattpilot.utils import (  # noqa: E402
    async_ConnectCharger,
    async_DisconnectCharger,
    async_GetChargerProp,
    async_SetChargerProp,
)

SECRETS_FILE = os.path.join(REPO_ROOT, ".wp_test.json")


async def main() -> None:
    cfg = json.load(open(SECRETS_FILE))
    con = cfg.get("connection", "local")
    data = {
        CONF_CONNECTION: con,
        CONF_PASSWORD: cfg["password"],
        CONF_TIMEOUT: int(cfg.get("timeout", 30)),
    }
    if con == CONF_CLOUD:
        data[CONF_SERIAL] = cfg["serial"]
    else:
        data[CONF_IP_ADDRESS] = cfg["ip"]

    print("1) async_ConnectCharger ...")
    charger = await async_ConnectCharger("live-e2e", data)
    if charger is False:
        sys.exit("   FAILED to connect (see logged error).")
    print(
        f"   connected: name={charger.name} connected={charger.connected} initialized={charger.properties_initialized}"
    )

    # 2) Live property push via the same callback mechanism the integration uses.
    pushes: list[tuple[str, object]] = []
    unsub = charger.on_property_change(lambda name, value: pushes.append((name, value)))
    print("2) waiting 8s for live property pushes ...")
    await asyncio.sleep(8)
    unsub()
    print(f"   received {len(pushes)} property pushes; sample codes: {sorted({p[0] for p in pushes})[:12]}")

    # 3) Reads through the integration helper.
    amp = await async_GetChargerProp(charger, "amp")
    fwv = await async_GetChargerProp(charger, "fwv", None)
    print(f"3) async_GetChargerProp: amp={amp!r} fwv={fwv!r}")

    # 4) SAFE no-op write: set amp back to its current value (no behaviour change).
    print(f"4) async_SetChargerProp: amp={amp!r} (no-op, same value) ...")
    ok = await async_SetChargerProp(charger, "amp", amp)
    await asyncio.sleep(2)
    print(f"   set_property returned: {ok}; amp now: {await async_GetChargerProp(charger, 'amp')!r}")

    print("5) async_DisconnectCharger ...")
    await async_DisconnectCharger("live-e2e", charger)
    print(f"   connected after disconnect: {charger.connected}")
    print("E2E OK" if ok and charger.connected is False else "E2E completed with warnings")


if __name__ == "__main__":
    asyncio.run(main())
