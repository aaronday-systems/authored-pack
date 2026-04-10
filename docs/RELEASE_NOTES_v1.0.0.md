# Authored Pack v1.0.0 Release Notes

Date: 2026-04-09
Status: released

## Release Summary

Authored Pack `v1.0.0` is the public deterministic core:
- `assemble`
- `verify`
- `inspect`
- `consume-bin`
- calm/noisy TUI flows
- `pack_root_sha256` and `payload_root_sha256`
- optional reproducible derived seed material

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

Backward compatibility:
- verification still accepts legacy `entropy.pack.v1` packs that carry `entropy_root_sha256.txt`
- new v1.0.0 packs emit only the primary root and derived-seed names

Stable public artifacts in V1:
- `manifest.json` (`authored.pack.v1`)
- `receipt.json` (`authored.receipt.v1`)
- public zip projection with final receipt state
- JSON CLI envelopes for `assemble`, `verify`, `inspect`, and `consume-bin`
- compatibility aliases remain available for `stamp` and `stamp-bin`

## Trust Boundary Notes

- Public receipts disclose the derivation inputs needed to reproduce the same derived seed material.
- Omitting `seed_master.*` from the public zip is not a secrecy control.
- Evidence bundles are tamper-evident local audit artifacts, not signed provenance.

## Release Verification

Release verification used for `v1.0.0`:
- `pytest -q`
- `python3 -m pytest -q`
- `python3 -m authored_pack --help`
- one real assemble/verify smoke run from the README commands
- clean public-safe tracked file set
