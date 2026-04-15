# Changelog

All notable public-release changes to Authored Pack will be documented here.

## [0.2.4] - 2026-04-15

### Changed
- rewrote the README front door around a clearer packet mental model instead of leading with examples
- moved concrete use cases to a dedicated `Suggested Use Cases` section later in the README
- added a worked explanation of `pack_root_sha256` versus `payload_root_sha256`
- clarified the three public surfaces: local pack directory, public zip projection, and local audit bundle

## [0.2.3] - 2026-04-14

### Changed
- surfaced the full operator verification policy in `verify` and `inspect` with `--max-manifest-mib`, `--max-artifact-mib`, and `--max-total-mib`
- documented that `assemble` remains unconstrained while `verify` and `inspect` enforce operator limits
- added the checked-in public voice brief and release-notes template used to keep future public surfaces consistent

### Fixed
- made reuse-time zip publication failure-atomic so a public receipt does not claim a zip before it exists
- made evidence-bundle zip publication atomic at the public-file level
- treat invalid verification-limit flags as usage errors instead of successful inspect output or bad-pack failures

## [0.2.2] - 2026-04-12

### Changed
- adopted Apache License 2.0 and removed the earlier proprietary/source-available wording
- kept the deterministic pack/verify contract stable while tightening the public release surface around the current open-source repo
- clarified the first-run README, demo, and product-origin framing for first-time engineers and agents

### Fixed
- release hygiene now matches the actual current commit: version surfaces, release notes, and package metadata all point at `v0.2.2`

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
