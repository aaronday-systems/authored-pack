# Canonical Demo

This is the one short honest demo for Authored Pack `v1.0.0`.

## What the demo shows

- what the user provides: a small folder of operator-supplied files
- what Authored Pack does: packages them into a deterministic pack with a manifest and receipt
- what comes out: a pack directory, pack root, payload root, and optional zip
- how to verify it: run `verify` on the directory pack and the zip
- why it matters: the pack is legible, auditable, and easy to hand off

## Runnable path

```bash
bash scripts/demo_v1.sh
```

## Manual equivalent

```bash
tmp="$(mktemp -d "${TMPDIR:-/tmp}/eps-demo.XXXXXX")"
mkdir -p "$tmp/input" "$tmp/out"
printf 'hello from Authored Pack\n' > "$tmp/input/note.txt"
printf 'operator supplied bytes\n' > "$tmp/input/context.txt"
printf '\x00\x01\x02' > "$tmp/input/sample.bin"

python3 -m eps stamp --input "$tmp/input" --out "$tmp/out" --zip
pack_dir="$(find "$tmp/out" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
python3 -m eps verify --pack "$pack_dir"
python3 -m eps verify --pack "$pack_dir/entropy_pack.zip"
```

## Demo notes

- Keep this demo in folder mode. Do not turn it into a secrecy story.
- Do not use `--derive-seed` in the first public demo.
- The simplest human explanation is:
  - you provide a folder
  - Authored Pack turns it into a verifiable pack
  - someone else can verify the same pack later
