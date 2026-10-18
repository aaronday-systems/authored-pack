# Authored Pack v0.2.6 Release Notes

Date: 2026-10-18
Status: released

## Release Summary

Authored Pack `v0.2.6` retains the public commands:
- `assemble`
- `verify`
- `inspect`
- `consume-bin`

This patch closes a temporary output file descriptor when opening the source for a copy fails. The existing destination remains intact and the temporary file is removed.

Regression tests now cover that failure path, HKDF output-length boundaries, bounded streaming reads, and source-bin defaults and filtering.

The README now leads with a practical handoff example, clarifies how to use the emitted ZIP path, and explains when a trusted expected root is needed.

## Product Boundary

Authored Pack provides deterministic packaging, canonical hashing, and verification. It does not provide randomness, automatic secrecy, signed provenance, or attestation.

## Public Contract Highlights

- Schema names remain `authored.pack.v1`, `authored.receipt.v1`, and optional `authored.evidence.v1`.
- Public verbs remain `assemble` and `consume-bin`; compatibility aliases remain available for `stamp` and `stamp-bin`.
- Pack formats and CLI behavior are unchanged.
- Apache License 2.0 remains the project license.

## Trust Boundary Notes

The fix improves resource cleanup after a failed source open. Verification still checks consistency with the presented manifest; it does not establish authorship or timestamp truth.

## Release Verification

Release verification used for `v0.2.6`:
- clean tracked worktree
- `bash scripts/release_check.sh --release-clean`
