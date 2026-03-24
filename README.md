# Entropy Pack Stamper (EPS)

Current release target: `v1.0.0`. Runtime version: `python3 -c 'from eps import __version__; print(__version__)'`.

EPS is source-available under the Aaron Day license. It is not OSI open source.

EPS stamps and verifies **EntropyPacks**: a directory (or `.zip`) containing:
- `manifest.json` (canonical, deterministic JSON)
- `receipt.json` (required for new v2 packs)
- payload artifacts (bytes)

New stamps emit:
- `manifest.json` with schema `entropy.pack.v2`
- `receipt.json` with schema `eps.receipt.v2`

It produces:
- `pack_root_sha256` (hex): `sha256(canonical_manifest_json)`
- `payload_root_sha256` (hex): `sha256(canonical_payload_artifact_record)`
- legacy alias `entropy_root_sha256` for older tooling that still expects the older name
- optional reproducible derived seed material (`seed_master`, 32 bytes) from HKDF over the pack root

EPS does not create entropy. It packages, commits, and verifies operator-supplied entropy-bearing inputs, then
optionally derives reproducible material from the rooted pack state.

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

## Why EPS Exists

Many agents are highly reproducible: given the same prompt, the same model, and the same inputs, you often get the same behavior.
That is useful, but it breaks down when an agent needs **fresh, unpredictable bits** for things like:
- generating keys/tokens/nonces
- creating one-time secrets for downstream systems
- preventing "replay" (the same run producing the same secret again)

In most environments you should use the OS CSPRNG (for example, `/dev/urandom` via your language runtime).
EPS exists for situations where you want **operator-supplied entropy-bearing material with auditability**: you can prove what bytes were packaged, and you can verify the resulting pack later.

## Why Seven Inputs

EPS uses **seven inputs** as a practical safety margin. It is not "seven types"; it is seven *independent inputs* (files, text, or tap timing).

Why multiple sources:
- Any single source can be low quality (a blurry photo, repetitive text, a short tap sequence).
- Mixing several sources makes it harder for one weak source to dominate the outcome.
- Operationally, it encourages a repeatable checklist for humans and for agents.

The TUI supports staging sources like photos, text, and tap timing. The headless `stamp-bin` mode selects seven files at random from an entropy bin and moves them into a pack path.

## What EPS Does (Plain English)

EPS treats each artifact as a **byte stream**:
1. It walks the input directory deterministically.
2. For each file, it computes `sha256(bytes)` and records `size_bytes`.
3. It writes a canonical `manifest.json` (sorted, stable JSON).
4. The pack identity is `pack_root_sha256 = sha256(canonical_manifest_json)`.
5. EPS also records `payload_root_sha256`, which stays stable for the payload artifact record even when pack-level metadata changes.

Optionally, EPS derives a 32-byte `seed_master` via HKDF from the rooted pack identity. When you choose to mix staged entropy sources, EPS also roots the derivation mode and staged-sources hash into the manifest so the pack identity and the derived seed both change if the mixed-source set changes.

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
- **Operator TUI**: default mode follows the Control Plane TUI baseline (`ssot/ui/*`); noisy mode is intentionally non-conforming.

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
- `docs/RELEASE_NOTES_v1.0.0.md` and `CHANGELOG.md` describe the public release surface.
- `CONTRIBUTING.md` and `SECURITY.md` define contribution and disclosure expectations for the public repo.

## TUI Contract

Normative reference for EPS UI behavior:
- `ssot/ui/TUI_STANDARD_v0.1.0.md`

Historical/reference-only:
- `ssot/ui/TUI_CONTRACT_v0.0.4.md`

Default mode follows the baseline contract. Noisy mode is intentionally non-conforming and is allowed to diverge for experimental visuals and motion.

## License

See `LICENSE`.
This repository is source-available under the Aaron Day license and is not OSI open source.

## UI Header Conformance
- Interactive terminal UIs must render header line 1 as `<App Name> :: <TUI Name> <SemVer>` for screenshot traceability.
