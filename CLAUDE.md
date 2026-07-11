# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Home Assistant (HA) Integration Development Standards

## 1. Context7 Documentation Rules
- Always use the Context7 MCP server to fetch version-accurate API references and code snippets before generating or modifying code that uses external libraries. Do not rely on base training data for fast-evolving frameworks.
- If asked to implement or modify features using frameworks like Home Assistant Core or auxiliary dependencies, always precede your response by invoking the Context7 tools.
- Append "use context7" to your planning steps if you need to research the latest documentation. 

## 2. Architectural Guardrails
- **The Async Iron Law:** Never allow blocking code (e.g., `requests`, `time.sleep`, or synchronous file reads) in the main thread. Always wrap synchronous device calls in `await hass.async_add_executor_job()` or rewrite them natively using `aiohttp` or `asyncio`.
- **Data Coordination:** Always scaffold the integration using a central `DataUpdateCoordinator`. Individual entities must inherit from `CoordinatorEntity` and pull states from the coordinator's cached data, rather than querying the API directly to prevent rate-limiting.
- **UI-Driven Configuration:** Do not write YAML parsing routines. Generate UI-driven `ConfigFlow` components (`config_flow.py`) for initial setup and an `OptionsFlow` for changing parameters later without restarting Home Assistant.
- **Client Library Separation:** All raw API-specific network code, parsing, and authentication handling must live in a separate third-party Python client library (declared in the `manifest.json` `requirements` array). The custom component code should only orchestrate state translation.
- **HACS Layout Compliance:** Ensure the repository follows HACS layout standards. The `manifest.json` must explicitly contain a valid `"version"` key and a `"codeowners"` list. Generate a `hacs.json` file automatically in the project root.

## 3. Python Coding & Style Guidelines
- **Formatting:** Code must pass Ruff styling defaults with a 120-character line limit. Run `ruff format` and `ruff check --fix` before completing any file modification.
- **Import Conventions:** Order imports strictly by standard library, third-party, and then local modules. Ensure constants and dictionary keys are sorted alphabetically. Adhere strictly to Home Assistant's mandatory custom framework shortcut module bindings (e.g., import `homeassistant.util.dt` as `dt_util`).
- **Type Checking:** Every function signature must be fully typed (arguments and return types). Prefer concrete types over `Any` (some idiomatic HA `Any` remains — config-flow `user_input`, voluptuous schema dicts, raw-JSON payloads). Must pass strict (`mypy`-compliant) analysis; use `assert` to narrow types when Core context is ambiguous. Import and use structural types from the core (`HomeAssistant`, `ConfigEntry`, `DiscoveryInfoType`). Include a `py.typed` file in the package root to satisfy PEP-561 compliance.
- **Documentation:** Public methods must use Google-style docstrings. Comments must be complete sentences ending in a period.
- **Logging Restrictions:** Do not include the platform or domain name manually inside log strings (e.g., write `_LOGGER.error("Failed to connect")`, not `_LOGGER.error("[MyDomain] Failed to connect")`). Never log sensitive strings like API keys, tokens, or local passwords. Use `_LOGGER.debug` for developer diagnostics.
- **Native Constants:** Never hardcode states like `'on'`, `'off'`, `'unavailable'`, or metrics like `'C'`. Always import and use native constants from `homeassistant.const` (e.g., `STATE_ON`, `STATE_OFF`, `STATE_UNAVAILABLE`, `UnitOfTemperature.CELSIUS`).
- **Entity Naming:** Do not assign a raw string to the `_attr_name` property of an entity. Set `_attr_has_entity_name = True` and use localized device naming keys via translation strings inside the `strings.json` file.
- **Exception Handling:** Wrap external Python client calls in `homeassistant.exceptions.HomeAssistantError` variations (like `ConfigEntryNotReady`) to trigger safe auto-retries and elegant user-facing UI dialogs.


## What This Is

A Home Assistant custom integration (HACS-distributed) that controls Fronius Wattpilot
wallbox / EV charging devices. It wraps the unofficial, reverse-engineered
[`wattpilot` Python module](https://github.com/joscha82/wattpilot), which talks to the
charger over a WebSocket (locally on the LAN, or via the go-e cloud). There is no official
Fronius API — everything is built on that community library and may break at any time.

The installable component lives entirely in `custom_components/wattpilot/`. Everything else
(`packages/`, `doc/`, `info.md`, `set_values_test.py`) is documentation, HA config examples,
or manual test scaffolding.

## Build / Test / Lint

There is no build step (it is an HA custom component, copied into `config/custom_components/`).

- **Tests** — a pytest scaffold lives in `tests/`. Install with `pip install -r requirements_test.txt`
  and run `pytest`. Layers:
  - `test_yaml_catalogs.py` / `test_const.py` — dependency-free (pyyaml + isolated `const.py`
    load); validate the data-driven entity catalogs and manifest/const consistency.
  - `test_utils.py` — imports the integration (pulls in Home Assistant + the vendored `wattpilot`
    library, which needs `websocket-client`); tests `async_SetChargerProp` coercion and
    `GetChargerProp`. Skips cleanly if those deps are absent.
  - `conftest.py` provides `MockCharger` / `mock_charger` / `make_charger` — a fake charger with
    an `allProps` dict and a `send_update` recorder; use it instead of a live device.
  - `pytest.ini` disables the `pytest-homeassistant-custom-component` plugin
    (`-p no:homeassistant`) because its autouse event-loop fixtures clash with the plain
    `asyncio.run()` helpers. Re-enable it (and add its `hass` fixture) when writing full
    end-to-end integration tests.
- **Static analysis** — `./scan.sh` runs bandit, semgrep, mypy and pip-audit in a `.venv`, scoped
  to `custom_components/wattpilot` and excluding the vendored `wattpilot/` subtree. Note the code
  predates the strict-typing/style standards in the header above, so mypy/bandit will be noisy.
- **Manual/live check** — copy `custom_components/wattpilot/` into a running HA, restart, and read
  the debug logs. The codebase logs verbosely under the `custom_components.wattpilot` logger
  namespace — enable `logger` debug there to trace behaviour.
- `set_values_test.py` (repo root) is a **standalone manual script**, not part of the pytest
  suite. It talks directly to the vendored `wattpilot` library to probe/set raw charger
  properties against a real device (edit the IP/password placeholders first); it is the reference
  for how raw `send_update` / property values behave.
- The integration `version` is set in `custom_components/wattpilot/manifest.json` and must be
  bumped there for HACS releases.

## Architecture

### Vendored, dynamically-loaded `wattpilot` library
`utils.py` (`_dynamic_load_module`) loads the `wattpilot` package **from the local vendored
copy** at `custom_components/wattpilot/wattpilot/src/wattpilot/` if present, otherwise falls
back to the pip-installed one declared in `manifest.json` (`wattpilot>=0.2`). The module-level
`wattpilot = _dynamic_load_module('wattpilot')` in `utils.py` is the single import point — reuse
it, don't re-import elsewhere. The code defensively supports both older and newer library APIs
(e.g. `register_property_callback` vs `add_event_handler`; a manual `_wsapp.close()` disconnect
fallback for library versions without `disconnect()`).

### Data-driven entities (the core pattern)
Entities are **not hard-coded**. Each platform has a matching YAML catalog next to its Python
file (`sensor.yaml`, `switch.yaml`, `select.yaml`, `number.yaml`, `button.yaml`, `update.yaml`).
Each platform's `async_setup_entry` reads its YAML, iterates the entity definitions, and
instantiates one entity per definition. **To add or change an entity, edit the YAML** — you
usually do not touch Python. `sensor.yaml`'s header comment documents every supported field
(`source`, `id`, `uid`, `enum`, `firmware`, `variant`, `connection`, `value_id`, `namespace_id`,
`attribute_ids`, `default_state`, etc.).

An entity's value `source` is one of:
- `property` — a key in `charger.allProps` (the charger's live property dict). Push-capable.
- `attribute` — a Python attribute on the `Wattpilot` charger object (e.g. `carConnected`). Poll-only.
- `namespacelist` — an indexed item (`namespace_id`) inside a property that is a list of
  `SimpleNamespace` objects (e.g. RFID cards). `value_id` picks which namespace field is the state.

### Base entity: `entities.py::ChargerPlatformEntity`
All platform entities subclass this. It centralizes:
- `__init__` gating via `_check_firmware_supported` / `_check_variant_supported` (11 vs 22 kW) /
  `_check_connection_supported` (local vs cloud). Failing a check sets `_init_failed`, and the
  setup loop skips that entity (`if getattr(entity,'_init_failed', True): continue`).
- The `_state_attr` indirection: subclasses override which attribute holds the state
  (`sensor`/`number` use `_attr_native_value`; base uses `state`).
- Platform hooks subclasses override: `_init_platform_specific()` (extra `_attr_*` from cfg) and
  `_async_update_validate_platform_state()` (coerce/validate the raw value for that platform —
  e.g. switch maps true/false ↔ `STATE_ON`/`STATE_OFF` with optional `invert`).

### Push + poll hybrid update model
- **Push:** `__init__.py::async_setup_entry` registers `PropertyUpdateHandler` as the charger's
  property callback. On each property change the library fires it; `utils.py` bridges the
  library's (possibly non-async, non-HA-loop) thread into HA via
  `asyncio.run_coroutine_threadsafe` → `async_PropertyUpdateHandler`, which looks up the entity
  in the per-entry `push_entities` dict (keyed by property id) and calls its `async_local_push`.
  Only `source: property` entities register for push.
- **Poll:** entities whose `should_poll` is true (attribute/namespacelist sources, or a
  property still at its default state) fall back to `async_local_poll`.
- The same handler also **fires HA events** (`wattpilot_property_message`) for properties listed
  in `const.py::EVENT_PROPS` (`ftt`, `cak`), and drives optional property-change debug logging.

### Reading and writing charger values
Always go through the `utils.py` helpers rather than touching the charger object directly:
- Read: `GetChargerProp` / `async_GetChargerProp` (safe access into `charger.allProps`).
- Write: `async_SetChargerProp` — it type-coerces the value (bool/int/float/str, honoring an
  optional `force_type` / the entity's `set_type`) before calling `charger.send_update`. This
  mirrors the coercion logic in `set_values_test.py`.

### Config, per-entry state, and services
- `config_flow.py` is a multi-step flow: connection type → local (IP + password) or cloud
  (serial + password), plus a matching options flow. Schemas live in `configuration_schema.py`.
  Options edits go through `__init__.py::options_update_listener`, which reloads the config entry.
- Per-entry runtime state is stored under `hass.data[DOMAIN][entry_id]` with keys from `const.py`
  (`CONF_CHARGER` = the connected charger object, `CONF_PARAMS`, `CONF_PUSH_ENTITIES`,
  `CONF_DBG_PROPS`, etc.). `utils.py` has `async_GetChargerFromDeviceID` /
  `async_GetDataStoreFromDeviceID` to resolve these from a HA `device_id` (used by services).
- Services are defined in `services.py`, described for the UI in `services.yaml`, and registered
  in `__init__.py::async_setup_entry`: `disconnect_charger`, `reconnect_charger`, `set_goe_cloud`
  (enable/disable go-e cloud API), `set_debug_properties` (toggle property-change warning logs),
  `set_next_trip` (writes the `ftt` next-trip timestamp, with daylight-saving handling).

### Platforms
Registered in `const.py::SUPPORTED_PLATFORMS`: `button`, `number`, `select`, `sensor`, `switch`,
`update`. (`manifest.json` `dependencies` also lists `diagnostics`.) `diagnostics.py` provides
the redacted diagnostics download.

## Common Charger Property Codes

Values are addressed by terse short-codes — the `id` of an entity in the YAML catalogs and the
identifier passed to `GetChargerProp` / `async_SetChargerProp`. The list below is the subset
actually wired up in this repo; it is **not exhaustive**. The per-platform `*.yaml` files are the
source of truth for what this integration exposes. Most are `charger.allProps` keys; a few
sensors read Python attributes on the charger object instead (marked *attr*).

**Provenance (verified against the go-e reference):** The Wattpilot is Fronius-rebranded go-e
hardware, so many codes come from the go-e Charger API — the authoritative doc is now
[`API_KEYS_FIRMWARE/apikeys-de.md`](https://github.com/goecharger/go-eCharger-API-v2/blob/main/API_KEYS_FIRMWARE/apikeys-de.md)
(and `apikeys-en.md` / firmware-sorted variants alongside it; the old repo-root `apikeys-de.md`
path the YAML headers cite now 404s). **However, a large group of the codes this integration uses
are Fronius-specific extensions that do NOT appear anywhere in the go-e reference:** `cci`, `psm`,
`onv`, `fap`, `fam`, `ebe`, `ebo`, `ebt`, `ebv`, `pdte`, `pdt`, `fot`, `ful`, `fre`, `ftt`, `fte`,
`cae`, `cak`, `qsw`, `wcch`, `wccw` — essentially the PV-surplus, battery-boost, next-trip, and
go-e-cloud-key features. Treat the go-e doc as authoritative only for the confirmed codes; for the
Fronius-specific ones the `*.yaml` descriptions and `set_values_test.py` are the only reference.
Two further caveats: `cards` is documented by go-e but was **removed in go-e firmware 60.0**, and
`lmo` differs — go-e documents `Default=3 / Awattar=4 / AutomaticStop=5`, while Fronius remaps it
to the Default / Eco / Next Trip modes shown in `select.yaml`.

### State / status (mostly read-only sensors)
| Code | Meaning |
|------|---------|
| `car` | Car charging state (Unknown / Idle / Charging / WaitCar / Complete / Error) |
| `carConnected` *(attr)* | Plug state (no car / charging / ready / complete) |
| `AccessState` *(attr)* | Access mode (open / locked / auto) |
| `modelStatus` | Reason charging is (dis)allowed right now — read-only |
| `nrg` | Live charging power (W); also a list of per-phase voltage/current/power values |
| `wh` | Energy charged since the car was connected (Wh) |
| `eto` | Total lifetime energy charged (Wh) |
| `err` | Internal error state (None / FiAc / FiDc / Phase / Overtemp / …) |
| `tma` | Controller temperature (°C) |
| `loc` | Charger local time |
| `cus` / `ffb` / `lck` | Cable-unlock status / lock feedback / effective lock setting |
| `rssi`, `wst`, `ccw` | WiFi signal / WiFi status / WiFi connection info |
| `rbc`, `rbt` | Reboot counter / ms since last boot |
| `cci` | Connected solar inverter |
| `cards_0`…`cards_9` | RFID chip/card slots — `namespacelist` sources, `value_id: energy` |

### Charging control (writable)
| Code | Meaning |
|------|---------|
| `frc` | Force state — the Start/Stop/Force charging buttons (Neutral / Off / On) |
| `amp` | Max charging current per phase (A) |
| `lmo` | Charging mode (Default / Eco / Next Trip) |
| `psm` | Phase switch mode (1-phase / 3-phase / auto) |
| `acs` | Access control setting |
| `ust` / `bac` | Cable unlock behaviour / button lock level |
| `ct` | Selected car profile |
| `trx` | Active transaction chip/card (also the "Authenticate" button) |
| `rst` | Restart the charger (button) |
| `onv` | Firmware — drives the `update` platform |

### PV surplus / battery / pricing (writable config)
| Code | Meaning |
|------|---------|
| `fup` / `fap` | Enable PV-surplus charging / charge-pause toggle |
| `fst`, `fmt`, `fam` | Surplus start power (W) / min charge time (ms) / battery threshold (%) |
| `spl3`, `mpwst`, `mptwt` | 3-phase power level (W) / phase-switch delay / interval (ms) |
| `ebe`, `ebo`, `ebt`, `ebv` | Battery boost enable / type / discharge-until % / (raw set in test script) |
| `pdte`, `pdt`, `fot` | Discharge PV battery enable / level % / Ohmpilot temp threshold |
| `ful`, `awc`, `awp` | Lumina Strom/aWattar enable / country / max price (EUR cent) |
| `fre` | Remain in Eco mode after Next-Trip range reached |

### Next-trip charging (see `set_next_trip` service and `number.py`)
| Code | Meaning |
|------|---------|
| `ftt` | Next-trip timestamp — written by `set_next_trip`; also in `EVENT_PROPS` |
| `fte` | Next-trip energy target (Wh). Setting it forces `esk: true` (kWh instead of km) |
| `esk` | Next-trip distance unit flag (workaround in `number.py::async_set_native_value`) |
| `tds` | Daylight-saving mode — `set_next_trip` adds an hour when `tds == 1` |

### go-e cloud API (see `set_goe_cloud` service)
| Code | Meaning |
|------|---------|
| `cae` | Cloud API enabled flag (written to toggle the go-e cloud) |
| `cak` | Cloud API key returned after enabling — read-only; also in `EVENT_PROPS` |
| `sse` | Serial number (fallback for the charger device identifier) |

### Device metadata (used by `entities.py::device_info`)
`typ` (model), `var` (variant, `11`/`22` kW — also the `variant:` gate in YAML),
`sse` (serial), `onv` (firmware).

`const.py::EVENT_PROPS` (`ftt`, `cak`) lists the properties whose changes are re-broadcast as
`wattpilot_property_message` HA events. `utils.py::async_PropertyDebug` keeps an
`exclude_properties` list of noisy, high-frequency codes (e.g. `nrg`, `rssi`, `rbt`, `loc`,
`fbuf_*`, `pvopt_*`) that are suppressed from property-change debug logging.

## Conventions

- Every function body is wrapped in `try/except` that logs
  `"%s - <func>: <msg>: %s (%s.%s)"` with the entry/charger id, the exception string, and the
  exception's module/type, then returns a falsy sentinel (`False`/`None`) rather than raising.
  Match this style; failures are logged-and-degraded, not propagated.
- Charger property short-codes (e.g. `nrg`, `acs`, `amp`, `frc`) largely come from the go-e API;
  the field reference is
  https://github.com/goecharger/go-eCharger-API-v2/blob/main/API_KEYS_FIRMWARE/apikeys-de.md
  (the YAML headers link it). Note some codes used here (`ftt`, `fte`, `cae`, `cak`, `psm`, …) are
  Fronius-specific and absent from that doc — see the property-codes section above.
- Firmware/variant/connection gating strings in YAML: firmware uses comparison prefixes
  (`>=`, `<=`, `==`, `<`, `>`) against the charger firmware version; `variant` is `11`/`22`;
  `connection` is `local`/`cloud`.
- User-facing strings/translations live in `translations/en.json` and `translations/de.json`.
