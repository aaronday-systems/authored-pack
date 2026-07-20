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

printf '\n[2/3] verify zip\n'
"$python_bin" -m authored_pack verify --pack "$zip_path"

printf '\n[3/3] inspect zip summary\n'
inspect_json="$("$python_bin" -m authored_pack inspect --pack "$zip_path" --json)"
INSPECT_JSON="$inspect_json" "$python_bin" - <<'PY'
import json
import os

payload = json.loads(os.environ["INSPECT_JSON"])
result = payload["result"]
print("pack_type:", result["pack_type"])
print("pack_root_sha256:", result["pack_root_sha256"])
print("payload_root_sha256:", result["payload_root_sha256"])
print("artifact_count:", result["artifact_count"])
print("artifact_preview:")
for item in result.get("artifact_preview", [])[:3]:
    path = item.get("path", "")
    size = item.get("size_bytes")
    if isinstance(size, int):
        print(f"- {path} ({size} bytes)")
    else:
        print(f"- {path}")
PY

printf '\nNext on your own folder:\n'
printf '  python3 -m authored_pack assemble --input /path/to/your-folder --out ./out --zip\n'
