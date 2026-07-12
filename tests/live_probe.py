"""Read-only live probe for a physical Wattpilot charger (wattpilot_api).

Connects to the charger described in ``.wp_test.json`` (gitignored), waits for
initialisation, prints a NON-SECRET summary of its property surface, then
disconnects. It never writes to the charger and never prints secrets
(password, cloud key, wifi credentials).

Usage:
    cp .wp_test.example.json .wp_test.json   # then edit in your charger details
    .venv-test/bin/python tests/live_probe.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from wattpilot_api import Wattpilot
from wattpilot_api.exceptions import AuthenticationError, WattpilotError

# Property codes whose values may be sensitive; only presence is reported.
REDACT = {"cak", "wifis", "scan", "data", "dll", "ocppck", "ocppcc", "ocppsc"}
SECRETS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".wp_test.json")


def _load_config() -> dict:
    if not os.path.exists(SECRETS_FILE):
        sys.exit(f"Missing {SECRETS_FILE} — copy .wp_test.example.json and fill it in.")
    with open(SECRETS_FILE) as f:
        return json.load(f)


async def _run(cfg: dict) -> None:
    con = cfg.get("connection", "local")
    timeout = float(cfg.get("timeout", 30))
    if con == "cloud":
        charger = Wattpilot(host=cfg["serial"], password=cfg["password"], serial=cfg["serial"],
                            cloud=True, connect_timeout=timeout, init_timeout=timeout)
    else:
        charger = Wattpilot(host=cfg["ip"], password=cfg["password"], serial=cfg["ip"],
                            connect_timeout=timeout, init_timeout=timeout)

    print(f"Connecting ({con}) ...")
    await charger.connect()
    try:
        props = charger.all_properties
        print("Connected OK.")
        print(f"  name        : {charger.name}")
        print(f"  serial      : <redacted> (present={bool(charger.serial)})")
        print(f"  manufacturer: {charger.manufacturer}")
        print(f"  device_type : {charger.device_type}")
        print(f"  model (typ) : {props.get('typ')}")
        print(f"  variant(var): {props.get('var')}")
        print(f"  firmware    : {charger.firmware}")
        print(f"  initialized : {charger.properties_initialized}")
        print(f"  all_props   : {len(props)} keys")
        print(f"  redacted    : {sorted(k for k in props if k in REDACT)}")
        sample = {k: (props[k] if k not in REDACT else "<redacted>") for k in sorted(props)}
        print("  properties  :")
        print(json.dumps(sample, indent=2, default=str)[:4000])
    finally:
        await charger.disconnect()
        print("Disconnected.")


def main() -> None:
    cfg = _load_config()
    try:
        asyncio.run(_run(cfg))
    except AuthenticationError:
        sys.exit("Authentication failed — check the charger password.")
    except WattpilotError as err:
        sys.exit(f"Connection failed: {type(err).__name__}: {err}")


if __name__ == "__main__":
    main()
