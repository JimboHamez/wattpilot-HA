# Changelog

All notable changes to this project are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This repository is a downstream fork of
[mk-maddin/wattpilot-HA](https://github.com/mk-maddin/wattpilot-HA); see the README
for attribution.

## [Unreleased]

_Nothing yet._

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
