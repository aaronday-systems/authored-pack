# Changelog

All notable public-release changes to Authored Pack will be documented here.

## [0.2.1] - 2026-04-10

### Added
- public repo scaffolding: `CONTRIBUTING.md`, `SECURITY.md`, and GitHub Actions CI
- public release notes for `v0.2.1`
- explicit current-release contract language in `README.md`

### Changed
- kept Authored Pack focused on the deterministic pack/verify core
- promoted `pack_root_sha256` and `payload_root_sha256` as the primary public identities
- clarified that derived seed material is reproducible and not automatic secrecy
- clarified that sealed/break-glass mode is future design only, not part of the current runtime behavior
- reset the public release line to `v0.2.1`
- bumped runtime/package version to `0.2.1`

### Fixed
- public release hygiene: `.claude/` is ignored and local Claude settings are no longer tracked
- repo exposure docs now avoid workspace-specific absolute paths in public-facing artifacts
- restored the visible Sources drop zone in the TUI and made empty-source Enter open import
- aligned CLI help, bad `--pack` handling, and repo-local `consume-bin` defaults with the current agent-facing contract
- made the canonical release check fail on dirty tracked files

## [0.0.1] - 2026-03-30

### Changed
- aligned calm TUI amber with Control Plane's runtime amber (`172` on xterm-256)
- reset runtime/package version to `0.0.1`
