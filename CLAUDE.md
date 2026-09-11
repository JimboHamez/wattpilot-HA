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
- **UI-Driven Configuration:** Do not write YAML parsing routines. Generate UI-driven `ConfigFlow` components (`config_flow.py`) for initial setup, and change connection settings later through `async_step_reconfigure` rather than an `OptionsFlow` (this integration has no options flow — see the config-flow section below).
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
wallbox / EV charging devices. It wraps the unofficial, reverse-engineered async
[`wattpilot-api`](https://pypi.org/project/wattpilot-api/) library, which talks to the
charger over a WebSocket (locally on the LAN, or via the go-e cloud). There is no official
Fronius API — everything is built on that community library and may break at any time.

This repo is a **downstream fork** of [mk-maddin/wattpilot-HA](https://github.com/mk-maddin/wattpilot-HA)
that has diverged substantially (0.5.0 replaced the vendored synchronous `wattpilot` module with
async `wattpilot-api`, plus translated entities, discovery/reauth, quality-scale work, and
human-scale entity units). Issues are tracked in **this** repo, not upstream.

The installable component lives entirely in `custom_components/wattpilot/`. Everything else
(`packages/`, `doc/`, `info.md`, `set_values_test.py`) is documentation, HA config examples,
or manual test scaffolding.

## Build / Test / Lint

There is no build step (it is an HA custom component, copied into `config/custom_components/`).

- **Tests** — the suite lives in `tests/`. Install with `pip install -r requirements_test.txt`
  and run `pytest`. Coverage is a **quality-scale rule (Silver, ≥95%)** — check it with
  `pytest --cov=custom_components.wattpilot --cov-report=term-missing` before claiming a change is
  done. Layers:
  - `test_yaml_catalogs.py` / `test_const.py` — dependency-free (pyyaml + isolated `const.py`
    load); validate the data-driven entity catalogs and manifest/const consistency.
  - Everything else imports the integration (pulling in Home Assistant and the `wattpilot-api`
    client) and skips cleanly if those deps are absent. Roughly by target: `test_init.py` (entry
    setup/teardown and every failure branch), `test_utils.py`, `test_services.py`,
    `test_entity_base.py` (gating, availability, poll/push), `test_platform_setup.py` (the shared
    per-platform setup skeleton, parametrized over all six), `test_platform_entities.py` (what each
    platform does with a value), `test_config_flow.py` / `test_options_flow.py`,
    `test_availability.py`, `test_diagnostics.py`, `test_icons.py`, `test_setup.py`.
  - `conftest.py` provides `MockCharger` / `mock_charger` / `make_charger` — a fake charger with
    an `all_properties` dict and a `set_property` recorder; use it instead of a live device.
  - Because the repo convention wraps every function in `try/except`, **the error branches are
    most of the uncovered surface**. The established way to reach them is to patch the collaborator
    the function calls (`patch("custom_components.wattpilot.<module>.<helper>", side_effect=...)`)
    and assert on `caplog` — see `test_platform_setup.py` for the parametrized version.
  - `pytest.ini` sets `asyncio_mode = auto` and **enables** the
    `pytest-homeassistant-custom-component` plugin (it used to be disabled via `-p no:homeassistant`).
- **Static analysis** — `./scan.sh` runs bandit, semgrep, mypy and pip-audit in a `.venv`, scoped
  to `custom_components/wattpilot`. (Its exclusion logic for the old vendored `wattpilot/` subtree
  is now a no-op — the subtree is gone.) The code is ruff-formatted and strict-mypy clean; keep it
  that way (`ruff format`, `ruff check`). `.github/workflows/test.yml` **gates** on this:
  `pytest --cov-fail-under=95`, `ruff format --check` + `ruff check`, and `mypy` run as three
  separate jobs, with ruff and mypy pinned so a new release cannot break `main` without a
  deliberate bump.
- **Manual/live check** — copy `custom_components/wattpilot/` into a running HA, restart, and read
  the debug logs. The codebase logs verbosely under the `custom_components.wattpilot` logger
  namespace — enable `logger` debug there to trace behaviour.
- **Live device scripts** — `tests/live_probe.py` (read-only property dump), `tests/live_e2e.py`
  (integration helpers, one idempotent no-op write), `tests/live_schedule.py` (decodes the three
  schedule properties, runs them through `validate_ranges`, and writes `sch_week` back **unchanged**
  — the one write that proves the charger takes the nested dict `build_schedule` produces; passed
  on the Flex, fw 43.4, 2026-09-11, with the charger echoing one `sch_week` push) and
  `tests/live_reconfigure.py` (read-only; the config flow's charger-facing reconfiguration logic)
  run against a physical charger using
  `wattpilot-api`, reading its address/password from a gitignored `.wp_test.json` (see
  `.wp_test.example.json`). Never log or commit those credentials. `live_reconfigure.py` exists
  because the identity guard only works if a real charger reports `sse` — it does (verified on a
  Flex, `sse=91111999`), but no mock can establish that. Note it deliberately sends **one wrong
  password** to check the `invalid_auth` path.
- `tests/live_reauth.py` is the exception to the "live scripts are standalone" rule: it is a
  pytest module (run it explicitly — `pytest.ini`'s `python_files` does not match `live_*.py`,
  so a normal run skips it) that drives the real flows through a real `hass` against the
  physical charger. It needs the `socket_enabled` fixture **and** `pytest_socket.socket_allow_hosts`,
  because pytest-socket otherwise pins the allowlist to localhost. Caveat worth remembering: only
  its *reconfigure* test can detect the 0.8.0 entry-wiping bug — Home Assistant fires update
  listeners only when an entry actually changed, so a reauth that re-submits the same password
  changes nothing and triggers no listener.
- `set_values_test.py` (repo root) is a standalone manual script, ported to `wattpilot-api` and
  `.wp_test.json` alongside the `tests/live_*.py` scripts. It remains the reference for raw
  property values and the type-coercion order, but unlike those two it **writes real settings**
  (toggles battery boost, overwrites `acs`/`ocppcs`/`npd`/`ebv`/`cll`) — run it only against a
  charger you are happy to reconfigure.
- The integration `version` is set in `custom_components/wattpilot/manifest.json` and must be
  bumped there for HACS releases.

## Architecture

### The `wattpilot-api` client library
The integration talks to the charger through the maintained **async** `wattpilot-api` package
(`manifest.json` requirements: `wattpilot-api>=1.4.0`), imported normally:
`from wattpilot_api import Wattpilot`. It is a plain pip dependency — there is **no vendored
library and no dynamic module loading** (both were removed in 0.5.0, along with the old
synchronous `wattpilot` module and its `websocket-client` thread).

Consequences to keep in mind when editing:
- The client is natively `asyncio`, and its property callbacks fire **on Home Assistant's own
  event loop** — no `run_coroutine_threadsafe` bridging, no executor jobs.
- The property dict is `charger.all_properties` (not `allProps`), and writes go through
  `await charger.set_property(id, value)` (not `send_update`).
- `charger.on_property_change(cb)` registers an async callback and **returns an unsubscribe
  function**, stored in the entry's runtime data and called on unload.
- Errors come from `wattpilot_api.exceptions` (`WattpilotError`, `AuthenticationError`);
  `utils.py::async_ConnectCharger` maps them onto HA's `ConfigEntryNotReady` / reauth.
- `set_property` coerces values against the client's `wattpilot.yaml`, which it **reads from disk
  lazily on the first write** — blocking, on the event loop, which HA reports. `async_setup_entry`
  therefore calls `utils.py::async_PreloadApiDefinition`, which reads the file in an executor
  (`_load_api_definition`, `@cache`d — the ~200 ms parse is paid by the first entry set up, not at
  import, and later entries get it for free) and puts the result in the client's private
  `_api_def_cache`. Both halves degrade to the old lazy load if the library renames anything. It is
  a workaround for an upstream lazy load; drop it if the client ever loads it without blocking.

All three manual scripts now use `wattpilot-api` and read charger details from a gitignored
`.wp_test.json`: `tests/live_probe.py` (read-only), `tests/live_e2e.py` (integration helpers, one
no-op write) and `set_values_test.py` (repo root), which stays the record of raw
property/coercion behaviour but genuinely writes settings — see the Build/Test/Lint note above.

### Data-driven entities (the core pattern)
Entities are **not hard-coded**. Each platform has a matching YAML catalog next to its Python
file (`sensor.yaml`, `switch.yaml`, `select.yaml`, `number.yaml`, `button.yaml`, `update.yaml`).
**To add or change an entity, edit the YAML** — you usually do not touch Python.
`sensor.yaml`'s header comment documents every supported field
(`source`, `id`, `uid`, `enum`, `firmware`, `variant`, `connection`, `value_id`, `namespace_id`,
`attribute_ids`, `attribute_props`, `value_props`, `default_state`, etc.).

**Icons are the one exception to "edit the YAML".** They live in `icons.json`, keyed by platform
and then by the entity's translation key — `slugify(uid or id)`, the same key
`entities.py::__init__` assigns to `_attr_translation_key`. The catalogs no longer accept an
`icon:` field and no entity sets `_attr_icon`; Home Assistant serves `icons.json` and the
**frontend** resolves the icon per key, so an entity's state no longer carries an `icon`
attribute (quality-scale rule `icon-translations`). Adding an entity with an icon therefore means
touching both files — `tests/test_icons.py` fails on an orphaned key, a re-added YAML `icon:`, or
a non-`mdi:` value, and `tests/test_setup.py` asserts HA actually loads the file.

The setup skeleton itself is shared: **`catalog.py::async_setup_catalog_entities`** reads the
catalog, pulls the charger out of `entry.runtime_data`, builds one entity per definition and
calls `async_add_entities`. Each platform's `async_setup_entry` is a single call to it, passing
its entity class plus two knobs — `source` (a value source forced onto every definition;
`sensor` passes none, because its source varies per definition) and `required` (the keys a
definition must carry; only `update` extends the `("id", "source")` default). A definition whose
entity fails a gate sets `_init_failed` and is skipped there. Adding a platform means adding a
YAML catalog and an entity class, not another copy of the loop.

A `select` takes its options from a static dict (`options:`, slug-translated in `strings.json`),
or — `options_property:` — from a list property the charger reports (`clp` for the
`amp_preset` select). In the latter case `select.py` labels each raw value with itself, skips the
entity when the charger has no such list, resubscribes to that property so the options follow it,
and clears the selection (state unknown, at debug level) for a value with no option instead of
logging the error a static enum would.

A `sensor` with `schedule: true` (the three charging-schedule sensors) hands its object value to
`schedule.py::decode_schedule` instead of the base namespace handling: the state is the object's
`control` masked to the two known flag bits (so it always lands on an `enum` option), and the raw
`control`, both flags and the `HH:MM` windows become attributes. `schedule.py` is shared with the
`set_charging_schedule` service, which reads the current object, merges the supplied fields and
writes the whole thing back (`build_schedule`) — the charger cannot take a partial write of a
nested value. `async_SetChargerProp` passes a dict or list to the client as-is for that reason.

An entity's value `source` is one of:
- `property` — a key in `charger.all_properties` (the charger's live property dict). Push-capable.
- `attribute` — a Python attribute on the `Wattpilot` charger object (e.g. `carConnected`). Poll-only.
- `namespacelist` — an indexed item (`namespace_id`) inside a property that is a list of
  `SimpleNamespace` objects (e.g. RFID cards). `value_id` picks which namespace field is the state.

### Entity units: the charger's units are not the user's units
The charger reports raw ms / W / Wh. Several entities are presented in human-scale units, by two
different mechanisms — pick the right one:
- **`number.yaml` `factor:`** (raw charger units per entity unit). `number.py` divides the raw
  value by it on read and multiplies on write, so the charger still gets its native unit. Used
  because the *number* platform has unit converters only for a few device classes (temperature,
  reactive energy, volume flow rate) — **not** duration, energy or power. Current users:
  `fmt` (ms→min, 60000), `fte` (Wh→kWh, 1000), `fst` and `spl3` (W→kW, 1000).
  Caveat: this rescales the stored value, so changing a `factor:` is a breaking change for
  history and for any automation using the entity.
- **`sensor.yaml` `suggested_unit_of_measurement:`** — the sensor keeps its native unit and HA's
  own converter handles display, so long-term statistics stay continuous. Prefer this whenever
  the device class has a converter (the *sensor* platform has them for energy, power, and most
  others). Current users: `nrg` (W, shown kW); `eto`, `wh`, `cards_*`, `c0e`…`c9e`
  (Wh, shown kWh). Note extra state **attributes** (e.g. `nrg`'s `L1_Power`) are never
  unit-converted.

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
- **Push:** `__init__.py::async_setup_entry` registers an async callback with
  `charger.on_property_change(...)`, which calls `utils.py::async_PropertyUpdateHandler`. That
  handler **dispatches** the value over HA's dispatcher on a per-(entry, property) signal
  (`utils.py::property_update_signal`). Each `source: property` entity subscribes to its own
  signal in `entities.py::async_added_to_hass` (released via `async_on_remove`) and pushes the
  value into `async_local_push`. There is no central `push_entities` registry any more, and no
  cross-thread bridging — the callback already runs on the event loop.
- **Poll:** entities whose `should_poll` is true (attribute/namespacelist sources, or a
  property still at its default state) fall back to `async_local_poll`. Note the corollary: an
  entity whose real value happens to equal its `default_state` keeps polling.
- The same handler also **fires HA events** (`wattpilot_property_message`) for properties listed
  in `const.py::EVENT_PROPS` (`ftt`, `cak`), and drives optional property-change debug logging.
- **Availability logging:** the client reconnects its WebSocket on its own and exposes no
  connection-state callback, so `availability.py::ChargerConnectionMonitor` samples
  `connected` / `properties_initialized` every `const.py::AVAILABILITY_SCAN_INTERVAL` (30 s) and
  logs *only transitions* — one warning when the connection drops, one info when it returns
  (the quality-scale `log-when-unavailable` rule). `charger_available()` in the same module is
  the shared predicate; `entities.py::available` checks the same two flags per entity, at debug
  level. Started in `async_setup_entry`, its cancel callable is stored under
  `FUNC_CONNECTION_MONITOR` and invoked on unload.

### Reading and writing charger values
Always go through the `utils.py` helpers rather than touching the charger object directly:
- Read: `GetChargerProp` / `async_GetChargerProp` (safe access into `charger.all_properties`).
- Write: `async_SetChargerProp` — it type-coerces the value (bool/int/float/str, honoring an
  optional `force_type` / the entity's `set_type`) before awaiting `charger.set_property`. The
  coercion order (explicit `force_type` → bool → int → float → str) mirrors
  `set_values_test.py`.

### Config, per-entry state, and services
- `config_flow.py` is a multi-step flow: connection type → local (IP + password) or cloud
  (serial + password), plus mDNS/zeroconf discovery, a reauth flow and a reconfigure flow.
  Schemas live in `configuration_schema.py`; `async_get_RECONFIGURE_*_SCHEMA` build the
  prefilled forms the reconfigure step shows.
- **There is no options flow**, and `entry.data` is the only store — nothing reads
  `entry.options`. The options flow was removed once `async_step_reconfigure` existed (they
  edited the same connection fields, and the quality scale expects connection settings to live
  in reconfigure). Do not reintroduce an `add_update_listener` that copies `entry.options` onto
  `entry.data`: the old `options_update_listener` did exactly that, and because update listeners
  fire on **any** entry change, it emptied `entry.data` every time reauth or reconfigure
  finished. `tests/test_config_flow.py` runs both flows against a fully set-up entry to keep
  that from coming back.
- Reconfiguration keeps the entry's connection type (local stays local) and guards its identity:
  a cloud entry is its serial, so a changed serial aborts (`wrong_charger`); a local entry that
  stores a serial (zeroconf-discovered) is verified against the `sse` the reconnected charger
  reports; a manually added local entry is keyed by its IP, so the unique id moves with the
  address — after checking the new address is not another configured charger. The identity
  helpers are `_is_same_charger` / `_reconfigured_unique_id`, and `_async_validate_charger` is
  the shared "connect, and tell me which charger answered" primitive (`_async_test_connection`
  wraps it for the steps that only need the error).
- Per-entry runtime state lives on **`entry.runtime_data`** (a dict), *not* `hass.data[DOMAIN]`
  — keys from `const.py`: `CONF_CHARGER` (the connected charger object), `CONF_PARAMS`,
  `CONF_DBG_PROPS`, plus the `on_property_change` unsubscribe handle
  (`FUNC_PROPERTY_UPDATES_CALLBACK`) and the connection-monitor cancel callable
  (`FUNC_CONNECTION_MONITOR`). `utils.py` has `async_GetChargerFromDeviceID` / `async_GetDataStoreFromDeviceID` to
  resolve these from a HA `device_id` (used by services).
- Services are defined in `services.py`, described for the UI in `services.yaml`, and registered
  once in `__init__.py::async_setup` (not per entry): `disconnect_charger`, `reconnect_charger`, `set_goe_cloud`
  (enable/disable go-e cloud API), `set_debug_properties` (toggle property-change warning logs),
  `set_next_trip` (writes the `ftt` next-trip timestamp, with daylight-saving handling),
  `set_charging_schedule` (rewrites one of `sch_week` / `sch_satur` / `sch_sund` from the current
  object plus the supplied fields — see `schedule.py`).

### Platforms
Registered in `const.py::SUPPORTED_PLATFORMS` (a tuple): `button`, `number`, `select`, `sensor`,
`switch`, `update`. `diagnostics.py` provides the redacted diagnostics download. `manifest.json`
declares **no** `dependencies` — `async_forward_entry_setups` loads the platforms itself, and
that key is for integrations that must be set up before this one; do not re-add the platform
names to it.

## Quality scale

The integration targets the [HA Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/).
`manifest.json` declares `"quality_scale": "platinum"`, and
`custom_components/wattpilot/quality_scale.yaml` tracks every rule as `done`, `exempt` (with a
reason) or `todo`. **All four tiers are met** — every one of the 54 rules is `done` or `exempt`.

Keep the file honest — it is a self-declaration, and the `manifest.json` tier must not run ahead
of it:
- When adding a feature, update the affected rule(s) in `quality_scale.yaml` in the same change.
- Only raise the `manifest.json` tier when *every* rule of that tier is `done` or `exempt`.
- `exempt` always carries a `comment` explaining why the rule cannot apply.

State of the file (as of 0.6.4): **every rule is `done` or `exempt`** — the last two, Gold's
`exception-translations` (every raise in `services.py` carries a `translation_key`, backed by the
`exceptions` section of `strings.json` / `translations/*.json` and guarded by
`tests/test_exception_translations.py`) and `reconfiguration-flow` (`async_step_reconfigure`),
landed after 0.6.4. Platinum's `async-dependency` and `strict-typing` were already done via the
`wattpilot-api` move and the strict-mypy pass; `inject-websession` is exempt because the library
speaks `websockets`, not an aiohttp/httpx session.

`manifest.json` declares **platinum**, backed by an audit rather than by the file alone: the rule
list was diffed against `script/hassfest/quality_scale.py` in `home-assistant/core@dev` (54 rules,
none missing, none invented), the `brands` claim was confirmed against
`home-assistant/brands/custom_integrations/wattpilot`, and `strict-typing` rests on a clean
`mypy --strict` plus `py.typed` in both this package and the installed `wattpilot-api`. The two
weakest `docs-*` rules (`docs-use-cases`, `docs-examples`) and `docs-actions` were thin claims
until the README gained its **Use cases**, **Example automations** and **Actions** sections —
if you change what those sections cover, the rules go with them.

The Silver `action-exceptions` rule was in direct tension with the log-and-degrade convention
below, and the trade was settled deliberately: **`services.py` raises, everything else still logs
and degrades.** A service action is invoked by a person or a script, so its failure has to reach
the UI and stop the calling automation; an entity update or a background poll has no such caller.
Do not extend the raising style past `services.py` (entity command methods included) without
agreeing that separately.

## HACS packaging
`hacs.json` (repo root) declares the HACS metadata; `"homeassistant"` is the **minimum HA version**
and must not drift below what the code actually needs. It declares **2024.11**, set by the config
flow: `_get_reauth_entry`, `_get_reconfigure_entry` and `_abort_if_unique_id_mismatch` all arrived
in that release (`entry.runtime_data`, the previous floor, only needed 2024.6). Bump it whenever a
newer core API is adopted — and check the floor when adopting one, since nothing in the test suite
catches a too-low declaration. HACS installs from GitHub **releases**, so a version bump in
`manifest.json` only reaches users once a matching tag/release is cut.

## Common Charger Property Codes

Values are addressed by terse short-codes — the `id` of an entity in the YAML catalogs and the
identifier passed to `GetChargerProp` / `async_SetChargerProp`. The list below is the subset
actually wired up in this repo; it is **not exhaustive**. The per-platform `*.yaml` files are the
source of truth for what this integration exposes. Most are `charger.all_properties` keys; a few
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
Two further caveats: `cards` is documented by go-e but was **removed in go-e firmware 60.0** (and
is absent on Fronius firmware 43.4 — the flat `cNe`/`cNn`/`cNi` keys replace it), and
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
| `tma` | Controller temperature (°C) — a **list** of temperature sensors, not a scalar. Which indexes are populated depends on the hardware generation (older chargers 0–1; current ones 2–5, with 0–1 null — verified on a Flex, fw 43.4), so the sensor uses `value_reduce: max` rather than a fixed `value_id` |
| `loc` | Charger local time |
| `cus` / `ffb` / `lck` | Cable-unlock status / lock feedback / effective lock setting |
| `rssi`, `wst`, `ccw` | WiFi signal / WiFi status / WiFi connection info |
| `rbc`, `rbt` | Reboot counter / ms since last boot |
| `cci` | Connected solar inverter |
| `cards_0`…`cards_9` | RFID chip/card slots on firmware that still has the `cards` list — `namespacelist` sources, `value_id: energy` |
| `c0e`…`c9e` / `c0n`…`c9n` / `c0i`…`c9i` | The same ten slots on current firmware: energy (Wh) / card name / whether an RFID id is stored. Verified on a Flex (fw 43.4), where `cards` is absent |

### Charging control (writable)
| Code | Meaning |
|------|---------|
| `frc` | Force state — the Start/Stop/Force charging buttons (Neutral / Off / On) |
| `sch_week` / `sch_satur` / `sch_sund` | The app's charging times per day type — `{control, ranges: [{begin: {hour, minute, second}, end: {…}}]}`. `control` is a **bitmask** on Fronius firmware (1 = limit charging times, 2 = PV surplus outside them; 3 observed on fw 43.4), not the Disabled/Inside/Outside enum in the client's `wattpilot.yaml`. Read by the `schedule_*` sensors, written whole by `set_charging_schedule`; the app writes only the day type being edited |
| `amp` | Max charging current per phase (A) |
| `clp` | The app's charging-current presets, a list of A values (e.g. `[10, 16, 20, 24, 32]`) — the app's slider only stops at these. Backs the `amp_preset` select via `select.yaml` `options_property`, which writes `amp` |
| `lmo` | Charging mode (Default / Eco / Next Trip) |
| `psm` | Phase switch mode (1-phase / 3-phase / auto) |
| `acs` | Access control setting |
| `ust` / `bac` | Cable unlock behaviour / button lock level |
| `ct` | Selected car profile |
| `trx` | Active transaction chip/card — 1-based over the ten slots (1 = slot 0). Read twice: as the slot number ("ID Chip Current") and, through `value_props`, as that slot's name ("ID Chip Current Name"). Also the "Authenticate" button |
| `rst` | Restart the charger (button) |
| `onv` | Firmware — drives the `update` platform |

### PV surplus / battery / pricing (writable config)
| Code | Meaning |
|------|---------|
| `fup` / `fap` | Enable PV-surplus charging / charge-pause toggle |
| `fst`, `fmt`, `fam` | Surplus start power (W, shown kW) / min charge time (ms, shown min) / battery threshold (%) |
| `spl3`, `mpwst`, `mptwt` | 3-phase power level (W, shown kW) / phase-switch delay / interval (ms) |
| `ebe`, `ebo`, `ebt`, `ebv` | Battery boost enable / type / discharge-until % / (raw set in test script) |
| `pdte`, `pdt`, `fot` | Discharge PV battery enable / level % / Ohmpilot temp threshold |
| `ful`, `awc`, `awp` | Lumina Strom/aWattar enable / country / max price (EUR cent) |
| `fre` | Remain in Eco mode after Next-Trip range reached |

### Next-trip charging (see `set_next_trip` service and `number.py`)
| Code | Meaning |
|------|---------|
| `ftt` | Next-trip timestamp — written by `set_next_trip`; also in `EVENT_PROPS` |
| `fte` | Next-trip energy target (Wh, shown kWh). Setting it forces `esk: true` (kWh instead of km) |
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
- **Log level within that style:** a broad `except Exception` is a catch-all for something
  unexpected, so it logs with `_LOGGER.exception()` and carries a traceback. Reports of an
  expected condition — a validated-input failure outside any handler ("no id in yaml", "unknown
  device"), or a narrow handler catching a specific error — stay on `_LOGGER.error()`, since
  there is nothing to trace. `services.py::_raise_service_failure` is called *from* its handlers
  rather than being one, so it passes `exc_info=e` explicitly (ruff `LOG004` rejects a bare
  `.exception()` outside a handler).
- **`services.py` is the one exception** (quality-scale `action-exceptions`): its handlers keep the
  same `try/except` shape and the same log line, but end in a raise — `ServiceValidationError` for
  a bad call (missing parameter, unknown device, unusable value), `HomeAssistantError` for a valid
  call the charger could not carry out. Each handler re-raises `HomeAssistantError` untouched and
  funnels anything unexpected through `_raise_service_failure`, which logs it and returns the
  wrapping error. Resolve targets via `_required` / `_async_get_charger` / `_async_get_entry_data`
  so those validation errors stay uniform. Every raise passes `translation_domain=DOMAIN` and a
  `translation_key` (plus `translation_placeholders`, values stringified) instead of a literal
  message — quality-scale `exception-translations`. A new key means adding an `exceptions` entry
  to `strings.json`, `translations/en.json` **and** `translations/de.json`;
  `tests/test_exception_translations.py` fails on a missing key, an orphaned one, or a message
  interpolating a placeholder the raise does not pass.
- Charger property short-codes (e.g. `nrg`, `acs`, `amp`, `frc`) largely come from the go-e API;
  the field reference is
  https://github.com/goecharger/go-eCharger-API-v2/blob/main/API_KEYS_FIRMWARE/apikeys-de.md
  (the YAML headers link it). Note some codes used here (`ftt`, `fte`, `cae`, `cak`, `psm`, …) are
  Fronius-specific and absent from that doc — see the property-codes section above.
- Firmware/variant/connection gating strings in YAML: firmware uses comparison prefixes
  (`>=`, `<=`, `==`, `<`, `>`) against the charger firmware version; `variant` is `11`/`22`;
  `connection` is `local`/`cloud`.
- User-facing strings/translations live in `translations/en.json` and `translations/de.json`.
