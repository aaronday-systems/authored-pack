# Changelog

All notable public-release changes to Authored Pack will be documented here.

## [Unreleased]

## [0.2.5] - 2026-07-20

### Changed
- published the Apache-2.0 project from a curated minimal-history snapshot while preserving private workshop history outside the public repository
- removed private operations material, internal handoffs, legacy release drafts, and the unproven bundled word-list asset from the public snapshot
- replaced the internal arbitrary assembly finalizer callback with a narrow source-record adjunct input while preserving `assemble` and compatibility aliases
- made `consume-bin` disclose random source selection in help and list the original moved paths in human output
- made the canonical release check accept an existing contributor patch, add an explicit `--release-clean` mode, build and inspect wheel plus sdist, rebuild from the sdist, and smoke the installed console outside the checkout
- aligned the maintained Python range and CI on Python 3.11, 3.12, and 3.13
- corrected public trust-boundary wording so `pack_root_sha256` identifies the canonical manifest contract rather than every directory, receipt, or container byte

### Fixed
- made directory and ZIP verification fail closed on malformed JSON, manifests, receipts, paths, derived fingerprints, duplicate members, and bounded-read failures
- made new assembly and same-root reuse transactional across the pack directory, public ZIP, evidence bundle, sidecar, and optional source record
- restored `inspected_path` in roots-only JSON inspection and made invalid human inspection print detailed verifier errors
- made negative artifact-preview values and invalid default `consume-bin` paths fail as usage errors without JSON tracebacks
- made the TUI keep active Assemble and Sources selections visible, guard destructive quit/clear actions, accept Unicode input, reject oversized text instead of truncating, and defer watched-drop overflow without losing it
- hardened root-alias verification so malformed `pack_root_sha256.txt` and legacy alias contents fail consistently for directory, zip, and JSON verification paths
- clarified first-run README guidance to use the zero-exit `python3 -m authored_pack --help` command

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
- public release hygiene: local assistant settings are ignored and no longer tracked
- repo exposure docs now avoid workspace-specific absolute paths in public-facing artifacts
- restored the visible Sources drop zone in the TUI and made empty-source Enter open import
- aligned CLI help, bad `--pack` handling, and repo-local `consume-bin` defaults with the current agent-facing contract
- made the canonical release check fail on dirty tracked files

## [0.0.1] - 2026-03-30

### Changed
- aligned calm TUI amber on xterm-256 (`172`)
- reset runtime/package version to `0.0.1`
