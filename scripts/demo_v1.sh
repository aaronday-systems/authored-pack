#!/usr/bin/env bash
set -euo pipefail

tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/eps-demo.XXXXXX")"
input_dir="$tmp_root/input"
out_dir="$tmp_root/out"

mkdir -p "$input_dir" "$out_dir"

printf 'hello from EPS\n' > "$input_dir/note.txt"
printf 'operator supplied context\n' > "$input_dir/context.txt"
printf '\x00\x01\x02' > "$input_dir/sample.bin"

printf 'demo_dir=%s\n' "$tmp_root"
printf '\n[1/3] stamp\n'
python3 -m eps stamp --input "$input_dir" --out "$out_dir" --zip

pack_dir="$(find "$out_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
zip_path="$pack_dir/entropy_pack.zip"

printf '\n[2/3] verify pack dir\n'
python3 -m eps verify --pack "$pack_dir"

printf '\n[3/3] verify zip\n'
python3 -m eps verify --pack "$zip_path"

printf '\ninspect=%s\n' "$pack_dir"
