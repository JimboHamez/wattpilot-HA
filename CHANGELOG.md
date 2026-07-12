# Changelog

All notable changes to this project are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This repository is a downstream fork of
[mk-maddin/wattpilot-HA](https://github.com/mk-maddin/wattpilot-HA); see the README
for attribution.

## [Unreleased]

_Nothing yet._

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

[Unreleased]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/JimboHamez/wattpilot-HA/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/JimboHamez/wattpilot-HA/releases/tag/v0.4.0
