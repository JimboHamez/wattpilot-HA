# Changelog

All notable changes to this project are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This repository is a downstream fork of
[mk-maddin/wattpilot-HA](https://github.com/mk-maddin/wattpilot-HA); see the README
for attribution.

## [Unreleased]

_Nothing yet._

## [0.5.5] - 2026-07-17

Readable Charging Reason and Internal Error states, and a completed German translation.

### Changed
- **Charging Reason and Internal Error now read as prose instead of raw API codes.** Both sensors
  passed the charger's identifiers straight through to the UI in *both* languages — Charging Reason
  showed `ChargingBecausePvSurplus` and `NotChargingBecauseUnbalancedLoad`, Internal Error showed
  `FiAc` and `ContactorStuck`. They now read "Charging: PV surplus" / "Not charging: unbalanced
  load" and "Residual current (AC)" / "Contactor stuck", with matching German ("Lädt:
  PV-Überschuss", "Fehlerstrom (AC)").
  Only the display strings changed. The entity state remains the slug (`chargingbecausepvsurplus`),
  which `sensor.py` derives from the `sensor.yaml` enum, so **automations and history are
  unaffected**.

### Fixed
- **German translations completed for the aWATTar Country and ID Chip Current sensors.** Country
  names were left in English (Austria, Germany, Switzerland → Österreich, Deutschland, Schweiz),
  and the ID chip states now read "Kein Chip" / "Keine Transaktion" / "ID-Chip 0"–"ID-Chip 9".

- Entity updates await the poll coroutine directly. `async_update` and `async_local_push` wrapped
  `async_local_poll` in a tracked task and immediately awaited it, adding scheduler bookkeeping and
  detaching from the current cancellation scope for no benefit. The fire-and-forget
  `async_create_task` calls are unchanged, as they must not block their synchronous callers.

## [0.5.4] - 2026-07-13

Follow-up to 0.5.3, from a second live-charger restart.

### Fixed
- **ID Chip Current failed to be added.** Its `default_state: 999` (the "no transaction" sentinel)
  is a *charger* code, not a Home Assistant option, and 0.5.3 still wrote it as the entity's first
  state — HA rejected it with "provides state value '999', which is not in the list of options".
  Enum and timestamp sensors now always start at `None` regardless of `default_state`; the first
  poll supplies a validated value.
- **Access State and Car Connected showed a raw number** (e.g. `1` with no car connected). The old
  vendored library derived friendly strings from these; `wattpilot-api` returns the raw `acs` /
  `car` codes. They are now enum sensors with translated states — Access State reads Open / Wait,
  Car Connected reads No Car / Charging / Ready / Complete (mirroring the library's `AccessState`
  and `CarStatus`).

### Added
- English and German state translations for the two sensors above.

## [0.5.3] - 2026-07-13

Fixes for errors reported from a live charger on 0.5.2.

### Fixed
- **Missing entities: Access State and Car Connected.** The entity `id` was split on `_` for
  every source, so these attribute sensors looked for an `access` / `car` attribute (which does
  not exist) instead of `access_state` / `car_connected`, and were dropped at setup with
  "Charger does not have an attribute". Only a `namespacelist` id (`cards_0`) carries an index
  suffix; every other id is now used verbatim.
- **Entities failing to be added: ChargingReason, ID Chip current, Local Time.** The first state
  Home Assistant writes happens at add time, before any poll or push, and the STATE_UNKNOWN
  *string* is rejected by strict device classes — enum sensors ("state value 'unknown' … not in
  the list of options") and timestamp sensors ("has timestamp device class but provides state
  unknown"). Sensors now start from `None` (which HA renders as unknown) unless the catalog gives
  an explicit `default_state`.
- **'Unknown' entities after every restart.** The charger only pushes a property when it
  *changes*, and the first poll tick was up to 30 s away, so entities sat at unknown after a
  restart even though the value was already known. Every entity is now seeded from the charger as
  soon as it is added. This is the actual cause of **PV Surplus** and **Remain in Eco Mode**
  showing no on/off state — not the `0`/`1` encoding guessed at in 0.5.2 (the go-e API defines
  `fup` as a bool; the widened coercion is kept as it is still correct for other properties).
- Absent optional properties (`ffb`, `lck`, `tse`, `upo`, `ust`, RFID card slots beyond those the
  charger has) are no longer logged as **errors** at every setup. The catalogs are deliberately a
  superset of what any one model/firmware reports, so a missing value means "skip this entity" and
  is now logged at debug.

### Added
- Regression tests for entity identification and startup state (`tests/test_entity_init_state.py`).

## [0.5.2] - 2026-07-13

Entity units are now human-scale (minutes / kW / kWh instead of the charger's raw
milliseconds / watts / watt-hours), plus a switch-state fix. **Not yet validated against a live
charging session** — see the note on the switch fix below.

### Changed
- **BREAKING — entity units.** Several entities now present human-scale units instead of the
  charger's raw ones. The charger still receives its native units; the conversion is in the
  integration. See "Entity units" in the README.
  - **Min Charging Time** (`fmt`): milliseconds → **minutes** (1–60).
  - **Next Trip Charging** (`fte`): Wh → **kWh**, as a 5–120 kWh slider.
  - **Start Charging at** (`fst`): W → **kW**, bounded 1.4–22 (was effectively unbounded).
  - **3-Phase power level** (`spl3`): W → **kW**, bounded 0–22.
  - **Charging Power** (`nrg`): displayed in **kW**.
  - **Totally Charged** (`eto`), **Connection Charged** (`wh`) and the ID-chip energy
    counters: displayed in **kWh**.

  The four *numbers* are rescaled by the integration, so their recorded values change scale:
  history shows a step at the upgrade, and automations or dashboards referencing them in raw
  ms/W/Wh must be updated. The *sensors* keep their native unit and use Home Assistant's unit
  conversion, so their long-term statistics stay continuous. Per-phase power **attributes**
  (`L1_Power`, `TotalPower`, …) are not converted and remain in watts.
- `spl3`'s `device_class` corrected from `energy` to `power` — it is a power value in W, and the
  previous combination was an invalid unit/device-class pair.

### Added
- Number catalog: optional `factor:` key (raw charger units per entity unit) — the raw value is
  divided by it on read and multiplied on write.
- Sensor catalog: optional `suggested_unit_of_measurement:` key, so a sensor can keep its native
  unit while being displayed in another (honoured only where the device class has a unit
  converter).

### Fixed
- Boolean switches whose property is reported as `0`/`1` rather than `true`/`false` no longer get
  stuck at `unknown` after startup — the switch state coercion now accepts both encodings.
  (Suspected cause of **PV Surplus** and **Remain in Eco Mode** showing no on/off state; awaiting
  confirmation against a live charger.)

## [0.5.1] - 2026-07-12

Patch release fixing an empty charging-mode dropdown introduced by the 0.5.0
migration to `wattpilot-api`.

### Fixed
- The **Charging Mode** (`lmo`) and **Cable unlock** (`ust`) selects had no
  options: they sourced them from `charger.lmoValues` / `charger.ustValues`
  attributes that only existed on the old vendored library. Their options are
  now static (`lmo`: Default / Eco / Next Trip; `ust`: Normal / AutoUnlock /
  AlwaysLock), with matching English and German option translations, and are
  validated against a physical charger.

### Added
- Regression tests for the `lmo` / `ust` select options and slug round-trip.

## [0.5.0] - 2026-07-12

Major release advancing the Home Assistant Integration Quality Scale: migration to
the maintained **async** `wattpilot-api` library, translated entity names and states,
and mDNS discovery. Validated end-to-end against a physical Wattpilot Flex (22 kW,
firmware 43.4).

### Changed
- **BREAKING — dependency:** replaced the vendored, synchronous `wattpilot` library
  with the maintained async **`wattpilot-api` >= 1.4.0** (built on `asyncio` +
  `websockets`). This satisfies the quality-scale `async-dependency` rule; the
  thread-to-loop callback bridge is gone (property callbacks now run on the event
  loop). Requires **Python >= 3.12**.
- **BREAKING — entity names:** entities now use `has_entity_name` with translated
  names. Existing `entity_id`s are preserved (the `unique_id` is unchanged), but the
  displayed friendly name is now composed as `{device name} {entity name}`.
- **BREAKING — select options:** `select` option values are now stable slugs
  translated for display (e.g. `psm` exposes `auto` / `1_phase` / `3_phases`).
  Automations calling `select.select_option` with the old English labels must be
  updated to the slug values (the UI shows the translated label).
- Enum sensors (`car`, `err`, `modelstatus`, `wst`, `cus`, `ffb`, `lck`, `trx`) are
  now `enum` device-class entities with translated states.
- `iot_class` corrected to `local_push` (property updates are pushed, not polled).
- Bumped integration version to 0.5.0.

### Added
- **Connection validation during setup:** the config flow now tests the connection
  before creating the entry, so an invalid password or unreachable charger is reported
  inline instead of failing after setup.
- **Reauthentication flow:** an authentication failure (e.g. a changed charger
  password) now raises `ConfigEntryAuthFailed` and prompts the user to re-enter the
  password, instead of leaving the entry permanently failed.
- **mDNS/zeroconf discovery:** Wattpilot chargers advertising `_http._tcp.local.`
  with `devicefamily=wattpilot` are auto-discovered; the flow asks only for the
  password and keys the entry by serial (refreshing the stored IP on rediscovery).
- Complete config-flow, options-flow and service translations (English + German)
  matching the real flow steps, with `data_description` help text and a translatable
  connection-type selector. Replaces the previous stale translations that referenced
  a non-existent `charger` step.
- Translated entity names (74) and enum/option states (183) for English and German.
- `py.typed` marker (PEP 561).
- Config-flow tests covering the zeroconf discovery, confirm, already-configured and
  missing-serial paths.
- `PARALLEL_UPDATES` declared on every platform.
- Internal: per-entry state moved to `entry.runtime_data`; entities now receive
  pushed updates via a dispatcher subscription set up in `async_added_to_hass`
  (Bronze `runtime-data` / `entity-event-setup` / `test-before-configure`).
- Expanded documentation: configuration parameters, supported devices, the local-push
  data-update model, known limitations, troubleshooting, and removal instructions.

### Fixed
- Numeric sensors (e.g. `tma` charger temperature) and the new enum sensors no longer
  crash with `ValueError: … has the non-numeric value: 'unknown'` when the charger
  reports a missing value; a missing value now leaves the last value in place.

### Removed
- The vendored `wattpilot/` library copy and the dynamic module loader.

## [0.4.1] - 2026-07-11

Patch release fixing diagnostic-log crashes and spam reported on chargers running
newer firmware (where the go-e `cards` property was removed) and on the Local Time
sensor.

### Fixed
- `cards_*` sensors no longer crash with `'int' object is not subscriptable` when the
  `cards` property is absent (removed in go-e firmware 60.0). Namespacelist indexing is
  guarded everywhere, and affected entities report unavailable instead of raising.
- Absent charger properties are no longer logged at error level on every poll
  (`GetChargerProp: Charger does not have property: …`); lowered to debug.
- The Local Time (`loc`) sensor (`timestamp` device class) now parses the charger's
  datetime string into a timezone-aware `datetime` (handling the space before the UTC
  offset) instead of raising `'str' object has no attribute 'tzinfo'` on every update.

### Added
- Regression tests for the timestamp parsing and namespacelist indexing fixes.

## [0.4.0] - 2026-07-11

First release of this fork. Focus: correctness, tooling, and moving toward the Home
Assistant Integration Quality Scale.

### Added
- pytest suite: YAML-catalog, constants, `utils` value coercion, sensor timestamp,
  entity indexing, and end-to-end config-flow tests.
- GitHub Actions workflows: Tests, Validate (hassfest + HACS), and Security
  (bandit + pip-audit).
- `scan.sh` static-analysis helper (bandit, semgrep, mypy, pip-audit).
- `CLAUDE.md` with architecture notes and a charger property-code reference.

### Fixed
- Awaited `async_unload_entry` / `async_DisconnectCharger` in setup error paths (were
  fire-and-forget coroutines, so cleanup never ran).
- `update.py`: undefined `_attr_installed_version` name, a missing `return`, and a
  possibly-unbound `config_params`.
- `entities.py`: `description` property read a never-set attribute.
- `utils.py`: guard device-registry lookups against `None`.
- Several `_LOGGER` calls with mismatched format arguments and misleading docstrings.
- Config flow no longer swallows `AbortFlow`, so the duplicate-entry abort surfaces its
  real reason instead of a generic "exception".

### Changed
- Quality-scale (Bronze) improvements: `unique-config-entry` (abort on duplicate
  charger), `test-before-setup` (raise `ConfigEntryNotReady`), and `action-setup`
  (services registered in `async_setup`).
- `manifest.json`: added `integration_type` and `issue_tracker`, and sorted keys.

[Unreleased]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.5.5...HEAD
[0.5.5]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/JimboHamez/wattpilot-HA/releases/tag/v0.4.0
