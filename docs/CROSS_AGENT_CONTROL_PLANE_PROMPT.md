# Cross-Agent Prompt: Control Plane Architect

Context: This repo implements Entropy Pack Stamper (EPS), deterministic packaging and verification for operator-supplied entropy-bearing inputs, including a headless "entropy bin" mode for agents.

## What Was Added (2026-02-10)

- **Push-button headless mode:** `python3 -m eps stamp-bin`
  - Randomly selects **7** files from an entropy bin, **moves** them (subtractive), stamps them into a content-addressed pack under `--out`.
  - Default low-watermark policy: refuses to run if it would leave `< 50` files in the bin after consuming 7 (override with `--allow-low-bin`).
  - By default, also writes:
    - `entropy_pack.zip`
    - reproducible derived seed material (`derived_seed_fingerprint_sha256` recorded in `receipt.json` when seed derivation is enabled)
    - tamper-evident evidence bundle `eps_evidence_<root>.zip` + `.sha256`

- **Local bins instantiated (repo-local):**
  - Input bin: `./bins/entropy_bin`
  - Output bin: `./bins/eps_out`
  - Both directories are present in git with per-dir `.gitignore` that ignores all contents (safe for dropping entropy-bearing files locally).

## Quick Command (Agent-Friendly)

```bash
cd <repo-root> && \
python3 -m eps stamp-bin --json
```

The JSON envelope is machine-readable and should be treated as the contract:

- success: `{ "ok": true, "command": "...", "result": { ... } }`
- failure: `{ "ok": false, "command": "...", "error": { "type": "...", "message": "..." } }`

The `result` object includes `pack_dir`, `pack_root_sha256`, `payload_root_sha256`, legacy alias `entropy_root_sha256`, and a `receipt` payload. Conditional fields:
- `derived_seed_fingerprint_sha256` / legacy alias `seed_fingerprint_sha256` appear only when seed derivation is enabled
- `evidence_bundle_path` and `evidence_bundle_sha256` appear at the result level only when evidence bundle writing succeeds
- `derivation` appears only when seed derivation is enabled
- `entropy_sources_audit_status`, `entropy_sources_audit_requested_count`, `entropy_sources_audit_materialized_count`, and `entropy_sources_audit_warnings` appear when the TUI records source-audit state

## Notes For Control Plane Integration

- EPS outputs: `receipt.json` contains:
  - `pack_root_sha256`
  - `payload_root_sha256`
  - legacy alias `entropy_root_sha256`
  - `derived_seed_fingerprint_sha256` when seed derivation is enabled
  - `derivation` when seed derivation is enabled
  - `entropy_sources_audit_*` fields when source auditing is requested through the TUI

Do not treat omission of `seed_master.*` from the public zip as secrecy. If the receipt is public, the derived seed is reproducible from the disclosed inputs.
- "Untamperable" is currently implemented as **tamper-evident** (hashes + evidence manifest). True tamper-resistance would require an external signature step.
