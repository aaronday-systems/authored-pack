# EPS v0.2.0 Release Notes

Historical note: this is a pre-`Authored Pack v1.0.0` archive from the older `EPS` naming era.
It is not the current public contract and intentionally preserves legacy schema, artifact, and CLI terms for historical reference.

Date: 2026-03-22

## Contract changes

- New stamps now emit `entropy.pack.v2`.
- New receipts now emit `eps.receipt.v2`.
- `receipt.json` is part of the public pack contract for v2 packs and canonical public zips.
- `entropy_pack.zip` is now the canonical public artifact projection:
  - includes `manifest.json`, `pack_root_sha256.txt`, legacy alias `entropy_root_sha256.txt`, `receipt.json`, and `payload/**`
  - excludes `seed_master.*`, `entropy_sources/`, evidence zips, and `*.sha256`
- New stamps now also emit `payload_root_sha256` so payload equivalence can be tracked independently of pack-level metadata.

## Security and integrity changes

- Trusted local-file reads now use safe open/read helpers instead of plain pathname opens.
- Staging copies are hashed from the bytes actually copied into the temp pack.
- v2 manifests root derivation metadata when seed derivation is enabled.
- v2 verification checks `receipt.json` presence and consistency with rooted manifest metadata.
- Existing content-addressed packs are strictly verified before reuse.
- Entropy-bin recovery now preserves failed staging directories under `.eps_failed/...` instead of risking entropy loss.

## CLI changes

- `eps` is now declared as a console script entrypoint.
- All `--json` commands use one envelope shape:
  - success: `{"ok":true,"command":"...","result":{...}}`
  - failure: `{"ok":false,"command":"...","error":{"type":"...","message":"..."}}`
- Expected operational failures no longer fall through to raw Python tracebacks.

## TUI changes

- Raw seed material is shown only in a one-shot reveal viewer; it is no longer persisted into the running log pane.
- The TUI reserves the `LOCKDOWN` label for mixed-source derivation only.
- Root-only derivation is labeled explicitly as `root-only seed`.
- Entropy-source audit status is persisted into `receipt.json`:
  - `entropy_sources_audit_status`
  - `entropy_sources_audit_requested_count`
  - `entropy_sources_audit_materialized_count`
  - `entropy_sources_audit_warnings`

## Backward compatibility

- `verify_pack()` remains backward-compatible with `entropy.pack.v1` packs and zips.
- New stamps always emit v2 manifests/receipts.

## Operational note

- `seed_master` remains deterministic. Published packs reproduce the same derived seed material. Treat it as reproducible operator data, not automatic secrecy.
- EPS is deterministic packaging and verification of operator-supplied entropy-bearing inputs, not an RNG.
- Evidence bundle metadata is now returned by the caller surface instead of being written back into `receipt.json`, which breaks the stale receipt/evidence cycle and keeps the bundle aligned with final pack state.
