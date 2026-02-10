# Cross-Agent Prompt: Control Plane Architect

Context: This repo `/Users/aaronday/dev/entropy-pack-stamper` implements Entropy Pack Stamper (EPS), including a headless "entropy bin" mode for agents.

## What Was Added (2026-02-10)

- **Push-button headless mode:** `python3 -m eps stamp-bin`
  - Randomly selects **7** files from an entropy bin, **moves** them (subtractive), stamps them into a content-addressed pack under `--out`.
  - Default low-watermark policy: refuses to run if it would leave `< 50` files in the bin after consuming 7 (override with `--allow-low-bin`).
  - By default, also writes:
    - `entropy_pack.zip`
    - derived seed (`seed_fingerprint_sha256` recorded in receipt)
    - tamper-evident evidence bundle `eps_evidence_<root>.zip` + `.sha256`

- **Local bins instantiated (repo-local):**
  - Input bin: `/Users/aaronday/dev/entropy-pack-stamper/bins/entropy_bin`
  - Output bin: `/Users/aaronday/dev/entropy-pack-stamper/bins/eps_out`
  - Both directories are present in git with per-dir `.gitignore` that ignores all contents (safe for dropping entropy files locally).

## Quick Command (Agent-Friendly)

```bash
cd /Users/aaronday/dev/entropy-pack-stamper && \
python3 -m eps stamp-bin \
  --entropy-bin ./bins/entropy_bin \
  --out ./bins/eps_out \
  --json
```

The JSON includes `entropy_root_sha256`, pack path(s), and evidence bundle hash for downstream agent ingestion.

## Notes For Control Plane Integration

- EPS outputs: `receipt.json` contains:
  - `entropy_root_sha256`
  - optional `seed_fingerprint_sha256`
  - optional `evidence_bundle_path` + `evidence_bundle_sha256`
- "Untamperable" is currently implemented as **tamper-evident** (hashes + evidence manifest). True tamper-resistance would require an external signature step.
