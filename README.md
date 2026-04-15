# Authored Pack

Authored Pack turns a folder of files into a deterministic pack you can verify later.

It writes:
- `manifest.json`
- `receipt.json`
- `pack_root_sha256.txt`
- `payload_root_sha256` in manifest and receipt
- optional `authored_pack.zip`
- optional evidence bundle

It is not attestation, signed provenance, secrecy tech, or an RNG.

## Quick Start

From a clone of this repo:

```bash
git clone https://github.com/aaronday-systems/authored-pack.git
cd authored-pack
bash scripts/demo_v1.sh
```

If you want the raw commands:

```bash
python3 -m authored_pack assemble --input ./my_case --out ./out --zip
python3 -m authored_pack verify --pack ./out/<pack_root_sha256>/authored_pack.zip
python3 -m authored_pack inspect --pack ./out/<pack_root_sha256>/authored_pack.zip --json
```

Use system Python 3.11+ from a clone.
Most first-time users can start with `python3 -m authored_pack` and ignore the TUI.
Use the TUI when you want to stage notes, photos, or other manual sources before assembling a pack.
Run it with `python3 -B bin/authored_pack.py`.

## Core Mental Model

Think packet, not archive:

- reviewed folder in, deterministic packet out
- `assemble` produces the packet
- `verify` and `inspect` are the receiver-side admission surface
- `pack_root_sha256` identifies the whole packet
- `payload_root_sha256` identifies the payload bytes only

The local pack directory is the working object.
`authored_pack.zip` is the public projection you hand to another person, job, or agent.

## Why This Exists

Authored Pack came out of earlier work on agent seed state.

The original question was whether indistinct initialization causes downstream failures in behavior, security, or drift. The first practical response was to gather deliberate traces from outside the machine, notes, taps, photos, and other small session artifacts, and assemble them into one bounded input set. That work originally used the name `Entropy Pack Stamper`.

The name was wrong. It implied randomness, secrecy, and security properties the tool did not have. Once that was stripped away, the durable core was obvious: deterministic assembly of a small artifact set into one reviewable pack with a stable root and a clear receipt.

That is Authored Pack now: a deterministic pack-and-verify tool. Not an entropy source. Not a proof system. Not an attestation engine.

The earlier history still explains the shape of the repo: manual staging, operator review, receipts, and bounded artifact sets instead of a generic archive tool.

## Use It When

- you want to hand off a bounded folder to another engineer or agent
- you want a deterministic pack with a stable root and receipt
- you want someone else to verify the same bytes later

## Don't Use It When

- you need secrecy
- you need randomness
- you need signed provenance or attestation
- you just want a generic backup or archive tool

## What You Get

An assembled pack under `--out/<pack_root_sha256>/` containing:
- `manifest.json`
- `pack_root_sha256.txt`
- `receipt.json`
- `payload/`
- optional `authored_pack.zip`
- optional `authored_evidence_<root>.zip` plus `.sha256`

`pack_root_sha256` is the identity of the whole pack.
`payload_root_sha256` is the identity of the payload bytes only.

Concrete example:
- change `--notes` and the pack root changes
- keep the payload files the same and the payload root stays the same

## Pack Roots

Use `pack_root_sha256` when the whole packet contract matters.
Use `payload_root_sha256` when you care only about the payload bytes.

Worked example:

```bash
python3 -m authored_pack assemble --input ./case --out ./out_a --zip --notes "first review"
python3 -m authored_pack assemble --input ./case --out ./out_b --zip --notes "second review"
```

Expected result:

- the payload files are the same, so `payload_root_sha256` stays the same
- the packet metadata changed, so `pack_root_sha256` changes

That is the intended split between payload identity and packet identity.

## Share Surfaces

- pack directory: local working object with payload copy, manifest, receipt, and optional local artifacts
- `authored_pack.zip`: public projection for transport and later verification
- `authored_evidence_<root>.zip`: local audit adjunct, not signed provenance

If you are handing the packet to someone else, the normal public object is `authored_pack.zip`.

## Verify

```bash
python3 -m authored_pack verify --pack /path/to/pack_dir
python3 -m authored_pack verify --pack /path/to/authored_pack.zip
python3 -m authored_pack inspect --pack /path/to/pack_dir --json
```

Verification checks self-consistency of the presented pack against its manifest.
It does not establish authorship, timestamp truth, secrecy, or signed provenance.
By default, `verify` and `inspect` enforce operator caps for manifest size, single-artifact size, and total artifact bytes.
Use `--max-manifest-mib`, `--max-artifact-mib`, and `--max-total-mib` to tune that operator policy.
`assemble` remains unconstrained; the size-policy boundary lives on `verify` and `inspect`.

## Trust Boundary

Authored Pack gives you deterministic packaging, hashing, and verification.
It does not give you fresh randomness, secrecy, signed provenance, or sealed storage.

Advanced note: `--derive-seed` deterministically derives extra seed bytes from the pack root and receipt inputs.
Most first runs can ignore it.
If `receipt.json` is public, treat derived seed material as reproducible, not secret.

Compatibility aliases `stamp` and `stamp-bin` still work, but `assemble` and `consume-bin` are the public verbs.

Platform support target:
- macOS terminals
- Linux terminals
- TUI audio cues are best-effort and may stay silent if no supported local WAV player is available

Run tests:

```bash
pytest -q
```

## For Automation and Agents

Have the receiver `inspect` or `verify` before acting on a packet.
`verify` and `inspect` are the operator-policy surface for verification limits.
If you need to accept larger packs, raise `--max-manifest-mib`, `--max-artifact-mib`, or `--max-total-mib` explicitly.
`assemble` does not apply those limits by default.

## Consume Bin

`consume-bin` is the subtractive machine path for a disposable source bin.
Use it when an agent or script is draining a staging folder one pack at a time.

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

## Suggested Use Cases

- `Bug repro bundle`
  Put a failing fixture, a short note, and any logs into one folder, then hand the pack or zip to another developer or agent.
- `Debug session freeze`
  Freeze a debugging session by packaging screenshots, shell output, notes, and small fixture files into one deterministic packet.
- `External review packet`
  Curate the exact files, screenshots, and notes you want a vendor, reviewer, or consultant to inspect.
- `Design review packet`
  Package the exports, screenshots, and short context note that define one design review slice.
- `Lab run bundle`
  Freeze one experiment or bench session with notes, small measurement exports, plots, and setup photos.
- `Field capture packet`
  Stage notes, photos, and other small manual observations into one reviewed packet after a site visit or field session.
- `Session handoff bundle`
  Freeze a debugging, design, or research session by packaging screenshots, notes, exports, and small fixture files into one deterministic pack.
- `Manual source bundle`
  Use the TUI to stage short notes, photos, or other simple manual inputs, then assemble one reviewed pack from them.
- `Verified human-to-agent handoff`
  Curate the exact files and notes an agent should consume, then require `inspect` or `verify` before use.
- `Source-bin intake drain`
  Use `consume-bin` when a disposable staging folder should be drained into one bounded packet at a time.

## Public Release Boundary

Current release: `v0.2.4`.

Authored Pack is open source under Apache License 2.0.
It is OSI open source.

## Docs

- canonical demo: `docs/CANONICAL_DEMO.md`
- public copy assets: `docs/PUBLIC_COPY_ASSETS.md`
- release notes: `docs/RELEASE_NOTES_v0.2.4.md`
- contribution and disclosure policy: `CONTRIBUTING.md`, `SECURITY.md`

## License

Licensed under Apache License 2.0. See `LICENSE`.
