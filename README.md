# Authored Pack

Authored Pack turns a folder of files into a deterministic pack that another person or process can verify later.

It gives you reproducible packaging and integrity checks. It does not provide secrecy, randomness, signed provenance, or attestation.

## Quick Start

Requires Python 3.11 or newer.

```bash
git clone https://github.com/aaronday-systems/authored-pack.git
cd authored-pack
bash scripts/demo_v1.sh
```

To pack your own folder:

```bash
python3 -m authored_pack assemble --input ./my_case --out ./out --zip
python3 -m authored_pack verify --pack ./out/<pack_root_sha256>/authored_pack.zip
python3 -m authored_pack inspect --pack ./out/<pack_root_sha256>/authored_pack.zip --json
```

`assemble` creates the pack, `verify` checks it, and `inspect` summarizes it. Run `python3 -m authored_pack --help` for all options.

## What's in a Pack

| Path | Purpose |
| --- | --- |
| `payload/` | Copy of the input files |
| `manifest.json` | Canonical file records and pack metadata |
| `receipt.json` | Assembly result and summary |
| `pack_root_sha256.txt` | Root of the canonical manifest |
| `authored_pack.zip` | Optional transport copy |
| `authored_evidence_<root>.zip` | Optional local audit bundle |

The pack directory is the local working object. `authored_pack.zip` is the normal file to share.

## The Two Roots

- `payload_root_sha256` identifies the payload bytes.
- `pack_root_sha256` identifies the canonical manifest contract, including its payload root and metadata.

Changing only `--notes` changes the pack root but not the payload root.

`pack_root_sha256` is the SHA-256 of the manifest object's canonical JSON. It does not directly hash `receipt.json`, ZIP-container bytes, evidence bundles, source records, or seed files.

## What Verification Means

Verification checks the presented pack against its manifest. It does not establish authorship, timestamp truth, secrecy, or signed provenance.

Both directories and ZIP files can be verified. `verify` and `inspect` limit manifest size, artifact size, total payload bytes, and ZIP member count. Raise those limits explicitly with `--max-manifest-mib`, `--max-artifact-mib`, `--max-total-mib`, and `--max-zip-members`. `assemble` does not apply these receiver-side limits.

If you use `--derive-seed`, the result is deterministic. If the receipt is public, the derived bytes are reproducible rather than secret.

## Other Interfaces

### JSON

Commands with `--json` return a consistent envelope:

```json
{"ok":true,"command":"assemble","result":{...}}
{"ok":false,"command":"assemble","error":{"type":"ValueError","message":"..."}}
```

For the smallest receiver-side identity check, use `inspect --json --roots-only`.

### TUI

Use the optional TUI to stage notes, photos, or other manual sources:

```bash
python3 -B bin/authored_pack.py
```

It targets macOS and Linux terminals. Audio cues are best-effort.

### Consume a Source Bin

`consume-bin` drains a disposable staging folder into a pack:

```bash
python3 -m authored_pack consume-bin \
  --source-bin ./bins/source_bin \
  --out ./bins/authored_out
```

It randomly selects files and moves them into the completed pack. That selection is not a randomness or secrecy claim. By default it consumes 7 files and refuses to leave fewer than 50; `--allow-low-bin` waives that floor.

Compatibility aliases `stamp` and `stamp-bin` remain available, but the public verbs are `assemble` and `consume-bin`.

## When to Use It

Authored Pack fits bounded handoffs: bug reproductions, CI failures, field captures, external reviews, and stable QA fixtures.

Use another tool when you need secrecy, randomness, signed provenance, attestation, or a general-purpose backup archive.

## Why the Name Changed

An earlier version was called `Entropy Pack Stamper`.

The name was wrong. It implied randomness and security properties the tool did not have.

What remained was the useful part—deterministic assembly of a small artifact set into one reviewable pack. Not an entropy source. Not a proof system. Not an attestation engine.

## Project

Current release: [`v0.2.5`](https://github.com/aaronday-systems/authored-pack/releases/tag/v0.2.5).

Authored Pack is open source under the [Apache License 2.0](LICENSE).

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Canonical demo](docs/CANONICAL_DEMO.md)
- [Release notes](docs/RELEASE_NOTES_v0.2.5.md)
