# Authored Pack

Authored Pack is a small deterministic tool for assembling, verifying, inspecting, and exporting bounded artifact sets.

Current state: public v1 is the honest pack/verify core.

It assembles a directory into a content-addressed pack with:
- `manifest.json`
- `receipt.json`
- `pack_root_sha256`
- `payload_root_sha256`
- optional `authored_pack.zip`
- optional evidence bundle

It does not create entropy. It records a deterministic contract over the bytes you provide and can optionally derive reproducible material from that rooted pack state.

It is not a governed attestation system, a universal evidence runtime, or a proof product.
Governed attestation belongs to `Attestation Engine`.

## Quick Start

Run the honest end-to-end demo first:

```bash
bash scripts/demo_v1.sh
```

Or start the interactive path:

```bash
python3 -B bin/authored_pack.py
```

Or use the module entrypoint directly from the repo:

```bash
python3 -m authored_pack --help
```

Public support target for v1 is repo-local execution from a clone with system Python 3.11+.
Installed-CLI packaging flows are intentionally not the primary release contract.

## What You Provide

- a normal folder of files
- optionally, authored sources in the TUI if you want a more deliberate manual workflow

## What Authored Pack Produces

An assembled pack under `--out/<pack_root_sha256>/` containing:
- `manifest.json`
- `pack_root_sha256.txt`
- `receipt.json`
- `payload/`
- optional `authored_pack.zip`
- optional `authored_evidence_<root>.zip` plus `.sha256`

Identity model:
- `pack_root_sha256` is `sha256(canonical_manifest_json)`
- `payload_root_sha256` is the payload-artifact identity, separate from pack-level metadata
- `pack_root_sha256` is stable for the same manifest inputs
- receipt timestamps do not change the pack root

## Verify

```bash
python3 -m authored_pack verify --pack /path/to/pack_dir
python3 -m authored_pack verify --pack /path/to/authored_pack.zip
python3 -m authored_pack inspect --pack /path/to/pack_dir --json
```

Verification checks self-consistency of the presented pack against its manifest.
It does not establish authorship, timestamp truth, secrecy, or signed provenance.
If you need governed attestation or proof of what happened, use `Attestation Engine`.

## Trust Boundary

Authored Pack is:
- deterministic packaging
- hashing
- verification
- optional reproducible derivation from rooted pack state

Authored Pack is not:
- an RNG
- automatic secrecy
- signed provenance
- sealed storage

Derived seed material is reproducible from the pack root and receipt derivation inputs.
If `receipt.json` is public, the derived seed material should be treated as reproducible by anyone with that receipt.
Do not describe it as a secret unless you add a separate secret input outside the public pack contract.

## Install / Run

Run directly from a clone with system Python 3.11+:

- TUI: `python3 -B bin/authored_pack.py`
- TUI noisy mode: `python3 -B bin/authored_pack.py --noisy`
- Module entrypoint: `python3 -m authored_pack --help`

Primary public create verb: `assemble`
- compatibility alias kept for now: `stamp`

Primary subtractive bin verb: `consume-bin`
- compatibility alias kept for now: `stamp-bin`

Platform support target:
- macOS terminals
- Linux terminals
- TUI audio cues are best-effort and may stay silent if no supported local WAV player is available

## Canonical Commands

Assemble a directory:

```bash
python3 -m authored_pack assemble \
  --input /ABS/PATH/TO/ARTIFACTS_DIR \
  --out ./out \
  --zip \
  --evidence-bundle
```

Verify the resulting pack:

```bash
python3 -m authored_pack verify --pack ./out/<pack_root_sha256>
python3 -m authored_pack verify --pack ./out/<pack_root_sha256>/authored_pack.zip
```

Inspect the resulting pack:

```bash
python3 -m authored_pack inspect --pack ./out/<pack_root_sha256>
```

Today, export is the optional `authored_pack.zip` written during `assemble --zip`.

Run tests:

```bash
pytest -q
```

## Consume Bin

`consume-bin` is the subtractive machine path for a disposable source bin.
`stamp-bin` remains as a compatibility alias.

```bash
python3 -m authored_pack consume-bin \
  --source-bin ./bins/source_bin \
  --out ./bins/authored_out
```

Important behavior:
- it randomly selects files from the bin
- it moves those files into the assembled pack on success
- by default it refuses to leave fewer than 50 files in the bin after consuming 7
- use `--allow-low-bin` only when that lower-watermark policy is intentionally waived

Repo-local bins are pre-created and ignored by git:
- `./bins/source_bin`
- `./bins/authored_out`

## JSON Contract

All `--json` commands emit one envelope shape:

```json
{"ok":true,"command":"assemble","result":{...}}
{"ok":false,"command":"assemble","error":{"type":"ValueError","message":"..."}}
```

For `assemble`, the `result` object includes `pack_dir`, `pack_root_sha256`, `payload_root_sha256`, and `receipt`.
The compatibility alias `stamp` emits the same shape with `command: "stamp"` when invoked that way.
For `consume-bin`, the `result` object also includes `consumed`, `warnings`, and `policy`.
The compatibility alias `stamp-bin` emits the same shape with `command: "stamp-bin"` when invoked that way.
For `verify`, the `result` object includes verification counts and verifier errors.
For `inspect`, the `result` object includes pack roots, schema summary, verification status, and an artifact preview.

## Public Release Boundary

Current release: `v1.0.0`.
Sealed mode is not implemented in V1.

Public v1 is the deterministic pack/verify core:
- `assemble`
- `verify`
- `inspect`
- `consume-bin`
- calm/noisy TUI flows
- `pack_root_sha256` and `payload_root_sha256`
- optional reproducible derived seed material

Authored Pack is source-available under the Aaron Day license.
It is not OSI open source.

## Docs

- canonical demo: `docs/CANONICAL_DEMO.md`
- public copy assets: `docs/PUBLIC_COPY_ASSETS.md`
- release notes: `docs/RELEASE_NOTES_v1.0.0.md`
- contribution and disclosure policy: `CONTRIBUTING.md`, `SECURITY.md`

## License

See `LICENSE`.
