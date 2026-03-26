# Entropy Pack Stamper (EPS)

EPS is a small deterministic pack/verify tool for operator-supplied entropy-bearing inputs.

## Start Here

Human first path in an interactive terminal:
1. Start the TUI: `python3 -B bin/eps.py`
2. Stage sources if you need them.
3. Stamp a folder, then verify the resulting pack.
4. Use Noisy Mode only if you want ceremony cues.

The human TUI is the discoverability path. CLI remains available, but the first screen should help a stranger reach `stamp -> verify` before they read the deeper docs.

EPS stamps and verifies **EntropyPacks**: a directory (or `.zip`) containing:
- `manifest.json` (canonical, deterministic JSON)
- `receipt.json` (required for new v2 packs)
- payload artifacts (bytes)

It does not create entropy. It packages, commits, and verifies operator-supplied inputs, then optionally derives reproducible material from the rooted pack state.

It is not an RNG, not automatic secrecy, not signed provenance, and not sealed break-glass storage.

State: live `v1.0.0` deterministic core. Sealed/break-glass mode is deferred design work, not current runtime behavior.

Run next: `python3 -m eps --help`

Repo-local bins (pre-created, contents ignored by git):
- `./bins/entropy_bin`
- `./bins/eps_out`

Current release: `v1.0.0`. Runtime version: `python3 -c 'from eps import __version__; print(__version__)'`.

EPS is source-available under the Aaron Day license. It is not OSI open source.

New stamps emit:
- `manifest.json` with schema `entropy.pack.v2`
- `receipt.json` with schema `eps.receipt.v2`

It produces:
- `pack_root_sha256` (hex): `sha256(canonical_manifest_json)`
- `payload_root_sha256` (hex): `sha256(canonical_payload_artifact_record)`
- `pack_root_sha256` ignores operational metadata such as receipt timestamps; those do not change the rooted pack identity
- legacy alias `entropy_root_sha256` for older tooling that still expects the older name
- optional reproducible derived seed material (32 bytes; compatibility alias `seed_master`) from HKDF over the pack root

## V1 Contract

EPS `v1.0.0` is the public deterministic core:
- `stamp`
- `verify`
- `stamp-bin`
- calm/noisy TUI flows
- `pack_root_sha256` and `payload_root_sha256`
- optional reproducible derived seed material

What EPS is not:
- not an RNG
- not automatic secrecy
- not signed provenance
- not sealed break-glass storage

What is stable in V1:
- `entropy.pack.v2` manifests
- `eps.receipt.v2` receipts
- the JSON envelope emitted by `--json`
- the public pack/zip contract documented in this README

What is deferred:
- sealed break-glass mode remains future design only
- see `docs/SEALED_PACK_ARCHITECTURE.md`

## Human Workflow

The TUI supports staging sources like photos, text, and tap timing. Use it when a person should make the input deliberate and auditable.

The practical human flow is:
1. Stage sources or point `stamp` at a folder.
2. `stamp` the inputs into a content-addressed pack.
3. `verify` the resulting pack later or after handoff.

That is the path to optimize for in the TUI and in the docs that introduce it.

## Canonical Demo

Run one honest demo end to end:

```bash
bash scripts/demo_v1.sh
```

That demo:
- creates a tiny disposable input set
- stamps it into a pack and zip
- verifies the directory pack
- verifies the zip pack

Reference walkthrough:
- `docs/CANONICAL_DEMO.md`

## Machine Sidecar

`stamp-bin` is the push-button, subtractive sidecar for agents or operators who already have a disposable entropy bin.

Safe first live run: copy disposable sample inputs into `./bins/entropy_bin` or another throwaway bin first. `stamp-bin` move-consumes files on success.

```bash
python3 -m eps stamp-bin \
  --entropy-bin "./bins/entropy_bin" \
  --out "./bins/eps_out"
```

- It randomly selects **7 files** from an entropy bin.
- It **moves** (consumes) those files into a new pack.
- Point it at a disposable bin or copied inputs, not at your only source-of-truth folder.
- By default, it refuses to run if it would leave fewer than **50 files** in the bin after consuming 7.
- Use `--allow-low-bin` to proceed anyway (prints a warning).

## Why EPS Exists

In most environments, use the OS CSPRNG for fresh randomness, keys, tokens, and nonces.
EPS exists for a different job: **package operator-supplied entropy-bearing material with auditability** so you can prove what bytes were packaged, reproduce the same rooted identity later, and verify the pack in another environment.

## Why Seven Inputs

EPS uses **seven inputs** as a practical safety margin. It is not "seven types"; it is seven *independent inputs* (files, text, or tap timing).

Why multiple sources:
- Any single source can be low quality (a blurry photo, repetitive text, a short tap sequence).
- Mixing several sources makes it harder for one weak source to dominate the outcome.
- Operationally, it encourages a repeatable checklist for humans and for agents.

## What EPS Does

In plain English:
1. It walks the input directory deterministically.
2. It hashes each artifact and records path plus size.
3. It writes a canonical `manifest.json` and a readable `receipt.json`.
4. It computes `pack_root_sha256` for the full pack contract.
5. It computes `payload_root_sha256` for the payload artifact record.
6. It can optionally derive reproducible seed material from the rooted pack state.

### Deterministic Derived Seed Model

`seed_master` is deterministic from pack identity (`pack_root_sha256`) and, when mixed-source derivation is enabled, from the rooted staged-sources hash.
This means disclosure determines reproducibility:
- If someone has the published pack, they can reproduce the same derived seed material for that pack.
- If someone can reconstruct your private inputs before publication, they can reproduce the same result once they have the same manifest inputs.
- Treat seed confidentiality as an operational trust boundary, not automatic cryptographic secrecy.
- Omitting `seed_master.*` from `entropy_pack.zip` is not a secrecy control when `receipt.json` is public, because the receipt discloses the derivation inputs needed to reproduce the same derived seed material.

Design goals:
- **Split identities**:
  - `pack_root_sha256` identifies the full pack contract.
  - `payload_root_sha256` identifies the payload artifact record independently of pack-level metadata.
- **Deterministic root**: `pack_root_sha256` is deterministic; operational metadata (e.g. `receipt.json:stamped_at_utc`) does not affect the root.
- **No external deps**: stdlib-only Python.
- **Operator interfaces**: CLI and TUI are both supported; noisy mode is intentionally experimental.

## Install

No install required. Run with system Python 3.11+:

- TUI: `python3 -B bin/eps.py`
- TUI (noisy skin): `python3 -B bin/eps.py --noisy`
- TUI (legacy alias): `python3 -B bin/eps.py --insane`
- CLI: `python3 -m eps --help`
- Installed package entrypoint: `eps --help`

Platform support target:
- macOS terminals
- Linux terminals
- TUI audio cues are best-effort and may stay silent if no supported local WAV player is available

Noisy-mode header words now come from the bundled file:
- `assets/godel_words.txt`

If you pass `--godel-source`, use text/markdown files. PDF runtime extraction is disabled.

## Commands

### Run tests (canonical)

```bash
pytest -q
```

### Stamp a pack from a directory

```bash
python3 -m eps stamp \
  --input "/ABSOLUTE/PATH/TO/ARTIFACTS_DIR" \
  --out "./out" \
  --zip \
  --derive-seed \
  --evidence-bundle
```

Outputs are written under `--out/<pack_root_sha256>/`:
- `<root_sha256>/manifest.json`
- `<root_sha256>/pack_root_sha256.txt`
- `<root_sha256>/entropy_root_sha256.txt`
- `<root_sha256>/receipt.json`
- optional `<root_sha256>/entropy_pack.zip`
- optional `<root_sha256>/eps_evidence_<root>.zip` + `.sha256`

`pack_id` is manifest metadata only. It does not select the output directory name.
Evidence bundles are local tamper-evident adjuncts, not signed provenance.

### Verify a pack (dir or zip)

```bash
python3 -m eps verify --pack /path/to/entropy_pack.zip
python3 -m eps verify --pack /path/to/pack_dir
```

### JSON contract

All `--json` commands emit one envelope shape:

```json
{"ok":true,"command":"stamp","result":{...}}
{"ok":false,"command":"stamp","error":{"type":"ValueError","message":"..."}}
```

For `stamp`, the `result` object includes `pack_dir`, `pack_root_sha256`, `payload_root_sha256`, legacy alias `entropy_root_sha256`, and `receipt`.
For `stamp-bin`, the `result` object also includes `consumed`, `warnings`, and `policy` so machine callers can audit what was consumed and whether low-water rules were crossed.
Evidence bundle metadata, when present, is returned alongside the receipt instead of being written back into `receipt.json`.
For `verify`, the `result` object includes verification counts and any verifier errors.

## Trust Boundary Notes

For untrusted/third-party agents, do not hand over `seed_master` unless you explicitly want them to reproduce the same derived seed material.
Prefer:
- `pack_root_sha256` when you want to share the full pack commitment
- `payload_root_sha256` when you want to compare payload equivalence across different pack metadata
- a full pack when you want downstream consumers to verify or reproduce the same derived seed material

Do not describe `seed_master` as a secret unless you have added a separate secret input to the derivation.

## Public Repo Notes

- Public repo scope is the deterministic pack/verify tool only.
- Sealed mode is not implemented in V1.
- `docs/SEALED_PACK_ARCHITECTURE.md` is design work for a future versioned mode, not a promise about current runtime behavior.
- `docs/CANONICAL_DEMO.md` is the short runnable walkthrough.
- `docs/PUBLIC_COPY_ASSETS.md` is the source copy for posts, screenshots, and short demos.
- `docs/RELEASE_NOTES_v1.0.0.md` and `CHANGELOG.md` describe the public release surface.
- `CONTRIBUTING.md` and `SECURITY.md` define contribution and disclosure expectations for the public repo.

## TUI Modes

The TUI is a supported interface for staged/manual workflows.
Noisy mode is experimental and may diverge in visuals, motion, and local audio cues without changing pack outputs.

## License

See `LICENSE`.
This repository is source-available under the Aaron Day license and is not OSI open source.

## UI Header Conformance
- Interactive terminal UIs must render header line 1 as `<App Name> :: <TUI Name> <SemVer>` for screenshot traceability.
