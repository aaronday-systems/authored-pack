# EPS v1.0.0 Release Notes

Date: 2026-03-22
Status: public release target

## Release Summary

EPS `v1.0.0` is the public deterministic core:
- `stamp`
- `verify`
- `stamp-bin`
- calm/noisy TUI flows
- `pack_root_sha256` and `payload_root_sha256`
- optional reproducible derived seed material

This release does **not** introduce sealed break-glass runtime behavior. That work remains future design only in `docs/SEALED_PACK_ARCHITECTURE.md`.

## Product Boundary

EPS does not create entropy.

EPS packages, commits, and verifies operator-supplied entropy-bearing inputs, then can optionally derive reproducible material from rooted pack state.

EPS is:
- deterministic packaging
- canonical hashing
- verification
- optional reproducible derivation

EPS is not:
- an RNG
- automatic secrecy
- signed provenance
- sealed storage

## Public Contract Highlights

Primary public identities:
- `pack_root_sha256`
- `payload_root_sha256`

Compatibility aliases retained in V1:
- `entropy_root_sha256`
- `seed_fingerprint_sha256`

Stable public artifacts in V1:
- `manifest.json` (`entropy.pack.v2`)
- `receipt.json` (`eps.receipt.v2`)
- public zip projection with final receipt state
- JSON CLI envelopes for `stamp`, `verify`, and `stamp-bin`

## Trust Boundary Notes

- Public receipts disclose the derivation inputs needed to reproduce the same derived seed material.
- Omitting `seed_master.*` from the public zip is not a secrecy control.
- Evidence bundles are tamper-evident local audit artifacts, not signed provenance.

## Release Readiness

Before tagging `v1.0.0`, confirm:
- `pytest -q`
- `python3 -m pytest -q`
- `python3 -m eps --help`
- one real stamp/verify smoke run from the README commands
- clean public-safe tracked file set
