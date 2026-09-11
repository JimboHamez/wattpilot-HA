"""Live check of the charging-schedule codec against a physical charger.

Connects to the charger described in ``.wp_test.json`` (gitignored), decodes
the three schedule properties the way the *Charging Schedule* sensors do, then
makes ONE idempotent write: it rebuilds ``sch_week`` from its own decoded
values with ``build_schedule`` and writes it back unchanged, so the charger's
acceptance of the nested-dict format is verified without altering anything.
The echoed property is compared with what was sent.

It never prints secrets. Usage:

    .venv/bin/python tests/live_schedule.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from wattpilot_api import Wattpilot

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from custom_components.wattpilot.schedule import (  # noqa: E402
    SCHEDULE_PROPS,
    build_schedule,
    decode_schedule,
    parse_time,
    validate_ranges,
)
from custom_components.wattpilot.utils import async_GetChargerProp, async_SetChargerProp  # noqa: E402

SECRETS_FILE = os.path.join(REPO_ROOT, ".wp_test.json")


def _as_plain(value: object) -> object:
    """Return a namespace tree as plain dicts/lists so it can be compared and printed."""
    return json.loads(json.dumps(value, default=lambda o: o.__dict__))


async def main() -> None:
    """Run the live check."""
    with open(SECRETS_FILE, encoding="utf-8") as handle:
        cfg = json.load(handle)
    timeout = float(cfg.get("timeout", 30))
    charger = Wattpilot(
        host=cfg["ip"], password=cfg["password"], serial=cfg["ip"], connect_timeout=timeout, init_timeout=timeout
    )
    print("Connecting ...")
    await charger.connect()
    try:
        print(f"Connected: firmware={charger.firmware}")

        # 1) Decode every day type the way the sensors do.
        decoded: dict[str, dict] = {}
        for day_type, prop in SCHEDULE_PROPS.items():
            raw = await async_GetChargerProp(charger, prop)
            decoded[day_type] = decode_schedule(raw)
            print(f"1) {prop} raw    : {json.dumps(_as_plain(raw))}")
            print(f"   {day_type:9} decoded: {json.dumps(decoded[day_type])}")

        # 2) The charger's own windows must pass the service's validation.
        for day_type, sched in decoded.items():
            ranges = [(parse_time(r["begin"]), parse_time(r["end"])) for r in sched["ranges"]]
            validate_ranges(ranges)
            print(f"2) {day_type:9} windows pass validate_ranges: {[f'{b:%H:%M}-{e:%H:%M}' for b, e in ranges]}")

        # 3) One no-op write: sch_week rebuilt from its decoded values.
        current = decoded["weekdays"]
        payload = build_schedule(
            current["limit_charging_times"],
            current["pv_surplus_outside_times"],
            [(parse_time(r["begin"]), parse_time(r["end"])) for r in current["ranges"]],
        )
        before = _as_plain(await async_GetChargerProp(charger, "sch_week"))
        assert payload == before, f"rebuilt payload differs from the charger's object:\n{payload}\n{before}"
        print(f"3) payload equals the current sch_week object; writing it back unchanged: {json.dumps(payload)}")

        echoes: list[object] = []
        unsub = charger.on_property_change(
            lambda name, value: echoes.append(_as_plain(value)) if name == "sch_week" else None
        )
        ok = await async_SetChargerProp(charger, "sch_week", payload)
        await asyncio.sleep(3)
        unsub()
        after = _as_plain(await async_GetChargerProp(charger, "sch_week"))
        print(f"   async_SetChargerProp returned {ok}; pushes for sch_week: {len(echoes)}")
        print(f"   sch_week after: {json.dumps(after)}")
        print("LIVE OK" if ok and after == before else "LIVE FAILED")
    finally:
        await charger.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
