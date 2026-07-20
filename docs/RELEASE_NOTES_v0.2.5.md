# Authored Pack v0.2.5 Release Notes

Date: 2026-07-20
Status: released

## Release Summary

Authored Pack `v0.2.5` remains the current public deterministic core:
- `assemble`
- `verify`
- `inspect`
- `consume-bin`

This release hardens verification, transactional assembly, package construction, and TUI operator safety. It also establishes the public repository from a curated minimal-history snapshot under Apache License 2.0.

## Product Boundary

Authored Pack is:
- deterministic packaging
- canonical hashing
- verification

Authored Pack is not:
- an RNG
- automatic secrecy
- signed provenance
- an attestation engine

## Public Contract Highlights

- Schema names remain `authored.pack.v1`, `authored.receipt.v1`, and optional `authored.evidence.v1`.
- Public verbs remain `assemble` and `consume-bin`; compatibility aliases remain available for `stamp` and `stamp-bin`.
- Directory and ZIP verification now fail closed on malformed JSON, receipts, root aliases, paths, duplicate members, and bounded-read failures.
- Assembly and same-root reuse publish the pack directory, public ZIP, evidence bundle, sidecar, and optional source record transactionally.
- Package builds cover Python 3.11, 3.12, and 3.13 and verify wheel and sdist contents before release.

## Trust Boundary Notes

- `pack_root_sha256` identifies the canonical manifest contract. It does not directly hash `receipt.json`, ZIP-container bytes, evidence bundles, source records, or seed files.
- Verification establishes consistency with the presented manifest. It does not establish authorship, timestamp truth, secrecy, or signed provenance.
- `consume-bin` selects and moves source files. Its random selection is not a randomness or secrecy claim.

## Release Verification

Release verification used for `v0.2.5`:
- clean tracked worktree
- `bash scripts/release_check.sh --release-clean`
