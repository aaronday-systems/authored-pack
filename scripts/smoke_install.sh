#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/authored-pack-install-smoke.XXXXXX")"
input_dir="$tmp_root/input"
out_dir="$tmp_root/out"
python_bin="${PYTHON_BIN:-}"

if [[ -z "$python_bin" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  else
    echo "python3.11+ is required for repo CLI smoke" >&2
    exit 1
  fi
fi

"$python_bin" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("python3.11+ is required for repo CLI smoke")
PY

repo_cli() {
  (
    cd "$ROOT"
    "$python_bin" -m authored_pack "$@"
  )
}

mkdir -p "$input_dir" "$out_dir"
printf 'repo cli smoke\n' > "$input_dir/note.txt"
printf '\x00\x01\x02' > "$input_dir/sample.bin"

repo_cli --help >/dev/null

assemble_json="$(repo_cli assemble --input "$input_dir" --out "$out_dir" --zip --json)"
consumer_summary="$(
  ASSEMBLE_JSON="$assemble_json" "$python_bin" - <<'PY'
import json
import os
from pathlib import Path

payload = json.loads(os.environ["ASSEMBLE_JSON"])
assert payload["ok"] is True
assert payload["command"] == "assemble"
result = payload["result"]
pack_dir = Path(result["pack_dir"])
zip_path = Path(result["zip_path"])
assert pack_dir.is_dir()
assert zip_path.is_file()
print(json.dumps({
    "pack_dir": str(pack_dir),
    "zip_path": str(zip_path),
    "pack_root_sha256": result["pack_root_sha256"],
    "payload_root_sha256": result["payload_root_sha256"],
}))
PY
)"

pack_dir="$(
  CONSUMER_SUMMARY="$consumer_summary" "$python_bin" - <<'PY'
import json
import os

print(json.loads(os.environ["CONSUMER_SUMMARY"])["pack_dir"])
PY
)"
zip_path="$(
  CONSUMER_SUMMARY="$consumer_summary" "$python_bin" - <<'PY'
import json
import os

print(json.loads(os.environ["CONSUMER_SUMMARY"])["zip_path"])
PY
)"

verify_json="$(repo_cli verify --pack "$pack_dir" --json)"
VERIFY_JSON="$verify_json" "$python_bin" - <<'PY'
import json
import os

payload = json.loads(os.environ["VERIFY_JSON"])
assert payload["ok"] is True
assert payload["command"] == "verify"
PY

inspect_json="$(repo_cli inspect --pack "$zip_path" --json)"
CONSUMER_SUMMARY="$consumer_summary" INSPECT_JSON="$inspect_json" "$python_bin" - <<'PY'
import json
import os

consumer = json.loads(os.environ["CONSUMER_SUMMARY"])
payload = json.loads(os.environ["INSPECT_JSON"])
assert payload["ok"] is True
assert payload["command"] == "inspect"
result = payload["result"]
assert result["pack_type"] == "zip"
assert result["pack_root_sha256"] == consumer["pack_root_sha256"]
PY

printf 'repo_cli_smoke_consumer=%s\n' "$consumer_summary"
printf 'repo_cli_smoke_dir=%s\n' "$tmp_root"
