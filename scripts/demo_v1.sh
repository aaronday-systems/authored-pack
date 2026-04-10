#!/usr/bin/env bash
set -euo pipefail

tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/authored-pack-demo.XXXXXX")"
input_dir="$tmp_root/input"
out_dir="$tmp_root/out"
python_bin="${PYTHON_BIN:-python3}"

mkdir -p "$input_dir" "$out_dir"

printf 'hello from Authored Pack\n' > "$input_dir/note.txt"
printf 'demo context\n' > "$input_dir/context.txt"
printf '\x00\x01\x02' > "$input_dir/sample.bin"

printf 'demo_dir=%s\n' "$tmp_root"
printf '\n[1/3] assemble\n'
"$python_bin" -m authored_pack assemble --input "$input_dir" --out "$out_dir" --zip

pack_dir="$(find "$out_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
zip_path="$pack_dir/authored_pack.zip"

printf '\n[2/3] verify pack dir\n'
"$python_bin" -m authored_pack verify --pack "$pack_dir"

printf '\n[3/3] verify zip\n'
"$python_bin" -m authored_pack verify --pack "$zip_path"

printf '\ninspect=%s\n' "$pack_dir"
