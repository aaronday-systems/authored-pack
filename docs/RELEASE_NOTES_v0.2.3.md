# Authored Pack v0.2.3 Release Notes

Date: 2026-04-14
Status: released

## Release Summary

Authored Pack `v0.2.3` remains the current public deterministic core:
- `assemble`
- `verify`
- `inspect`
- `consume-bin`
- calm/noisy TUI flows
- `pack_root_sha256` and `payload_root_sha256`
- optional reproducible derived seed material

This release keeps the schemas and public command surface stable. It hardens publication behavior in the core pack path, surfaces the full operator verification policy in the CLI, and makes the public docs and release scaffolding match that contract.

This release does **not** introduce sealed break-glass runtime behavior. That work remains future design only in `docs/SEALED_PACK_ARCHITECTURE.md`.

## Product Boundary

Authored Pack does not create entropy.

Authored Pack packages and verifies normal folders or deliberately staged authored sources, then can optionally derive reproducible material from rooted pack state.

Authored Pack is:
- deterministic packaging
- canonical hashing
- verification
- optional reproducible derivation

Authored Pack is not:
- an RNG
- automatic secrecy
- signed provenance
- sealed storage

## Public Contract Highlights

Primary public identities:
- `pack_root_sha256`
- `payload_root_sha256`

Stable public artifacts in the current public release:
- `manifest.json` (`authored.pack.v1`)
- `receipt.json` (`authored.receipt.v1`)
- public zip projection with final receipt state
- JSON CLI envelopes for `assemble`, `verify`, `inspect`, and `consume-bin`
- compatibility aliases remain available for `stamp` and `stamp-bin`

Additional release changes:
- Apache License 2.0 remains the repository license
- `verify` and `inspect` now expose `--max-manifest-mib`, `--max-artifact-mib`, and `--max-total-mib`
- `assemble` remains unconstrained while `verify` and `inspect` enforce operator limits
- zip and evidence-bundle publication now avoid leaving partially published public artifacts on the main failure paths
- package metadata, README, and release references all point at `v0.2.3`

## Trust Boundary Notes

- Public receipts disclose the derivation inputs needed to reproduce the same derived seed material.
- Omitting `seed_master.*` from the public zip is not a secrecy control.
- Evidence bundles are tamper-evident local audit artifacts, not signed provenance.
- Verification limits are operator policy. If you need to accept larger packs, raise those caps explicitly at the CLI.

## Release Verification

Release verification used for `v0.2.3`:
- clean tracked worktree
- `bash scripts/release_check.sh`
