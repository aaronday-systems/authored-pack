# EPS v0.2.0 Release Notes

Date: 2026-03-22

## Contract changes

- New stamps now emit `entropy.pack.v2`.
- New receipts now emit `eps.receipt.v2`.
- `receipt.json` is part of the public pack contract for v2 packs and canonical public zips.
- `entropy_pack.zip` is now the canonical public artifact projection:
  - includes `manifest.json`, `entropy_root_sha256.txt`, `receipt.json`, and `payload/**`
  - excludes `seed_master.*`, `entropy_sources/`, evidence zips, and `*.sha256`

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

- `seed_master` remains deterministic. Published packs reproduce the same derived seed material. Treat seed disclosure as an operator trust-boundary decision, not as automatic secrecy.
