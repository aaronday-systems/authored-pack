# Canonical Demo

This is the one short honest demo for Authored Pack `v0.2.2`.

Run this from repo root:

```bash
bash scripts/demo_v1.sh
```

You should see:

- assemble output with `pack_dir` and `zip_path`
- one `verify` pass that prints `ok`
- an inspect summary with the pack root, payload root, and a short file preview

## Manual Equivalent

```bash
tmp="$(mktemp -d "${TMPDIR:-/tmp}/authored-pack-demo.XXXXXX")"
mkdir -p "$tmp/input" "$tmp/out"
printf 'hello from Authored Pack\n' > "$tmp/input/note.txt"
printf 'demo context bytes\n' > "$tmp/input/context.txt"
printf '\x00\x01\x02' > "$tmp/input/sample.bin"

python3 -m authored_pack assemble --input "$tmp/input" --out "$tmp/out" --zip
# use the printed zip_path from assemble in the next two commands
python3 -m authored_pack verify --pack /path/to/authored_pack.zip
python3 -m authored_pack inspect --pack /path/to/authored_pack.zip --json
```

Next on your own folder:

```bash
python3 -m authored_pack assemble --input /path/to/your-folder --out ./out --zip
```
