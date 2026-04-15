# Authored Pack v0.2.4 Release Notes

Date: 2026-04-15
Status: released

## Release Summary

Authored Pack `v0.2.4` remains the current public deterministic core:
- `assemble`
- `verify`
- `inspect`
- `consume-bin`
- calm/noisy TUI flows
- `pack_root_sha256` and `payload_root_sha256`
- optional reproducible derived seed material

This release keeps the schemas and command surface stable. It does not add new runtime behavior. It sharpens the public explanation of the tool so first-time engineers and agents can understand the packet mental model, packet roots, and handoff surfaces faster.

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
- the README now leads with a packet mental model instead of example-heavy framing
- suggested use cases now live later in the README instead of in the front door
- the public docs now explain the split between packet identity and payload identity more directly
- package metadata, README, and release references all point at `v0.2.4`

## Trust Boundary Notes

- Public receipts disclose the derivation inputs needed to reproduce the same derived seed material.
- Omitting `seed_master.*` from the public zip is not a secrecy control.
- Evidence bundles are tamper-evident local audit artifacts, not signed provenance.
- Verification limits are operator policy. If you need to accept larger packs, raise those caps explicitly at the CLI.

## Release Verification

Release verification used for `v0.2.4`:
- clean tracked worktree
- `bash scripts/release_check.sh`
