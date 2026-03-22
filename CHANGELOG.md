# Changelog

All notable public-release changes to EPS will be documented here.

## [1.0.0] - pending public release

### Added
- public repo scaffolding: `CONTRIBUTING.md`, `SECURITY.md`, and GitHub Actions CI
- public release notes for `v1.0.0`
- explicit V1 contract language in `README.md`

### Changed
- froze EPS public V1 around the deterministic pack/verify core
- promoted `pack_root_sha256` and `payload_root_sha256` as the primary public identities
- clarified that derived seed material is reproducible and not automatic secrecy
- clarified that sealed/break-glass mode is future design only, not part of V1 runtime behavior
- bumped runtime/package version to `1.0.0`

### Fixed
- public release hygiene: `.claude/` is ignored and local Claude settings are no longer tracked
- repo exposure docs now avoid workspace-specific absolute paths in public-facing artifacts
