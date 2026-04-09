#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/authored-pack-install-smoke.XXXXXX")"
venv_dir="$tmp_root/venv"
input_dir="$tmp_root/input"
out_dir="$tmp_root/out"
python_bin="${PYTHON_BIN:-}"

if [[ -z "$python_bin" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  else
    echo "python3.11+ is required for install smoke" >&2
    exit 1
  fi
fi

"$python_bin" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("python3.11+ is required for install smoke")
PY

"$python_bin" -m venv "$venv_dir"
install_log="$tmp_root/install.log"
install_mode=""
if "$venv_dir/bin/pip" install "$ROOT" >"$install_log" 2>&1; then
  install_mode="pip"
elif grep -Eq "No matching distribution found|Could not find a version that satisfies the requirement|invalid command 'bdist_wheel'" "$install_log"; then
  printf 'install_smoke: pip install unavailable in this environment; falling back to setup.py install\n' >&2
  PYTHONWARNINGS=ignore "$venv_dir/bin/python" "$ROOT/setup.py" install >/dev/null
  install_mode="setup.py-fallback"
else
  cat "$install_log" >&2
  exit 1
fi

mkdir -p "$input_dir" "$out_dir"
printf 'install smoke\n' > "$input_dir/note.txt"
printf '\x00\x01\x02' > "$input_dir/sample.bin"

"$venv_dir/bin/authored-pack" --help >/dev/null

assemble_json="$("$venv_dir/bin/authored-pack" assemble --input "$input_dir" --out "$out_dir" --zip --json)"
printf '%s' "$assemble_json" | grep -q '"command":"assemble"'
printf '%s' "$assemble_json" | grep -q '"ok":true'

pack_dir="$(find "$out_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

verify_json="$("$venv_dir/bin/authored-pack" verify --pack "$pack_dir" --json)"
printf '%s' "$verify_json" | grep -q '"command":"verify"'
printf '%s' "$verify_json" | grep -q '"ok":true'

inspect_json="$("$venv_dir/bin/authored-pack" inspect --pack "$pack_dir" --json)"
printf '%s' "$inspect_json" | grep -q '"command":"inspect"'
printf '%s' "$inspect_json" | grep -q '"ok":true'

printf 'install_smoke_mode=%s\n' "$install_mode"
printf 'install_smoke_dir=%s\n' "$tmp_root"
