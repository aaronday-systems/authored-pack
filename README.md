# Entropy Pack Stamper (EPS)

EPS stamps and verifies **EntropyPacks**: a directory (or `.zip`) containing:
- `manifest.json` (canonical, deterministic JSON)
- payload artifacts (bytes)

It produces:
- `entropy_root_sha256` (hex): `sha256(canonical_manifest_json)`
- optional `seed_master` (32 bytes) derived via HKDF from the root

Design goals:
- **Deterministic root**: `entropy_root_sha256` is deterministic; operational metadata (e.g. `receipt.json:stamped_at_utc`) does not affect the root.
- **No external deps**: stdlib-only Python.
- **Operator TUI**: follows the Control Plane TUI baseline (`ssot/ui/*`).

## Install

No install required. Run with system Python 3.11+:

- TUI: `python3 -B bin/eps.py`
- TUI (insane skin): `python3 -B bin/eps.py --insane`
- CLI: `python3 -m eps --help`

## Commands

### Stamp a pack from a directory

```bash
python3 -m eps stamp \
  --input "/ABSOLUTE/PATH/TO/ARTIFACTS_DIR" \
  --out "./out" \
  --zip \
  --derive-seed \
  --evidence-bundle
```

Outputs are written under `--out`:
- `<pack_id-or-root>/manifest.json`
- `<pack_id-or-root>/entropy_root_sha256.txt`
- `<pack_id-or-root>/receipt.json`
- optional `<pack_id-or-root>/entropy_pack.zip`
- optional `<pack_id-or-root>/eps_evidence_<root>.zip` + `.sha256`

### Push-button mode: stamp from an entropy bin (subtractive)

This mode is for agents/operators who do not want to manage inputs manually.
It randomly selects **7 files** from an entropy bin, **moves** (consumes) them, and stamps them into a new pack.

```bash
python3 -m eps stamp-bin \
  --entropy-bin "/ABSOLUTE/PATH/TO/ENTROPY_BIN" \
  --out "./out"
```

By default, it refuses to run if it would leave fewer than **50 files** in the bin after consuming 7.
Use `--allow-low-bin` to proceed anyway (prints a warning).

#### Repo-local bins (pre-created)

This repo includes pre-created bins (contents ignored by git):
- `./bins/entropy_bin` (drop entropy files here)
- `./bins/eps_out` (stamped outputs go here)

Quick run:

```bash
python3 -m eps stamp-bin --json
```

### Verify a pack (dir or zip)

```bash
python3 -m eps verify --pack /path/to/entropy_pack.zip
python3 -m eps verify --pack /path/to/pack_dir
```

## Trust Boundary Notes

For untrusted/third-party agents, do not hand over the full pack. Prefer:
- root-only (`entropy_root_sha256`), and/or
- seed-only (`seed_master`, injected per-run and discarded)

## TUI Contract

Pinned references (copied from Control Plane):
- `ssot/ui/TUI_STANDARD_v0.1.0.md`
- `ssot/ui/TUI_CONTRACT_v0.0.4.md`

## License

See `LICENSE`.
