# Authored Pack vX.Y.Z Release Notes

Maintainer note: copy this file to `docs/RELEASE_NOTES_vX.Y.Z.md` for each release, then replace placeholders. Keep the public voice factual and bounded. This template is not part of the runtime or schema contract.

Date: YYYY-MM-DD
Status: released

## Release Summary

Authored Pack `vX.Y.Z` remains the current public deterministic core:
- `assemble`
- `verify`
- `inspect`
- `consume-bin`

State the smallest truthful summary of what changed in this release.

## Product Boundary

State what changed without widening the product claim.

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

List contract-relevant changes only:
- public verbs
- compatibility aliases
- schema stability
- release-line or license changes
- CLI / TUI contract shifts

## Trust Boundary Notes

Call out any trust-sensitive details that matter for operators or downstream engineers.

## Release Verification

Release verification used for `vX.Y.Z`:
- clean tracked worktree
- `bash scripts/release_check.sh`
