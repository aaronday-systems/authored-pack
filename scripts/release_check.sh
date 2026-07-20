#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_clean=false

case "${1:-}" in
  "") ;;
  --release-clean) release_clean=true ;;
  *)
    echo "usage: bash scripts/release_check.sh [--release-clean]" >&2
    exit 2
    ;;
esac

resolve_python_bin() {
  local candidate
  for candidate in \
    "${PYTHON_BIN:-}" \
    python3.13 python3.12 python3.11 python3 \
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
    /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11
  do
    [[ -n "$candidate" ]] || continue
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
    then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  echo "release_check: Python 3.11+ is required; CI covers 3.11, 3.12, and 3.13" >&2
  return 1
}

run_step() {
  local label="$1"
  shift
  printf '\n==> %s\n' "$label"
  (
    cd "$ROOT"
    "$@"
  )
}

python_bin="$(resolve_python_bin)"

if ! "$python_bin" -c 'import build' >/dev/null 2>&1; then
  echo "release_check: Python package 'build' is required (python -m pip install build)" >&2
  exit 1
fi

require_release_clean_tree() {
  (
    cd "$ROOT"
    if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules -- \
      || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
      echo "release_check: release mode requires a fully clean worktree" >&2
      git status --short --branch >&2
      exit 1
    fi
  )
}

tracked_fingerprint() {
  (
    cd "$ROOT"
    git diff --binary HEAD | "$python_bin" -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  )
}

if [[ "$release_clean" == true ]]; then
  run_step "release-clean tree" require_release_clean_tree
fi

starting_tracked_fingerprint="$(tracked_fingerprint)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/authored-pack-release-check.XXXXXX")"

finish() {
  local status=$?
  local ending_tracked_fingerprint
  ending_tracked_fingerprint="$(tracked_fingerprint)"
  if [[ "$starting_tracked_fingerprint" != "$ending_tracked_fingerprint" ]]; then
    echo "release_check: checks changed tracked files" >&2
    git -C "$ROOT" status --short >&2
    status=1
  fi
  rm -rf "$work_dir"
  trap - EXIT
  exit "$status"
}
trap finish EXIT

source_copy="$work_dir/source"
dist_dir="$work_dir/dist"
mkdir -p "$source_copy" "$dist_dir"
(
  cd "$ROOT"
  tar \
    --exclude='./.git' \
    --exclude='./.control' \
    --exclude='./.claude' \
    --exclude='./.local_scratch' \
    --exclude='./.pytest_cache' \
    --exclude='./dist' \
    --exclude='./build' \
    --exclude='./out' \
    --exclude='./authored_pack.egg-info' \
    --exclude='*/__pycache__' \
    -cf - .
) | tar -xf - -C "$source_copy"

run_step "pytest" "$python_bin" -m pytest -q
run_step "module help" "$python_bin" -m authored_pack --help
run_step "tui pty smoke" "$python_bin" scripts/smoke_tui_pty.py
run_step "repo cli consumer smoke" env PYTHON_BIN="$python_bin" bash scripts/smoke_install.sh
run_step "demo smoke" env PYTHON_BIN="$python_bin" bash scripts/demo_v1.sh

printf '\n==> build wheel and sdist\n'
"$python_bin" -m build --sdist --wheel --outdir "$dist_dir" "$source_copy"
wheel_path="$(find "$dist_dir" -maxdepth 1 -type f -name '*.whl' -print -quit)"
sdist_path="$(find "$dist_dir" -maxdepth 1 -type f -name '*.tar.gz' -print -quit)"
[[ -n "$wheel_path" && -n "$sdist_path" ]] || {
  echo "release_check: wheel or sdist was not produced" >&2
  exit 1
}

printf '\n==> inspect distribution contents\n'
"$python_bin" - "$wheel_path" "$sdist_path" <<'PY'
from __future__ import annotations

import sys
import tarfile
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import PurePosixPath

wheel_path, sdist_path = sys.argv[1:]
with zipfile.ZipFile(wheel_path) as zf:
    wheel_names = set(zf.namelist())
    metadata_names = [name for name in wheel_names if name.endswith(".dist-info/METADATA")]
    if len(metadata_names) != 1:
        raise SystemExit(f"wheel must contain one METADATA file: {sorted(metadata_names)}")
    wheel_metadata_raw = zf.read(metadata_names[0])
required_wheel = {
    "authored_pack/__init__.py",
    "authored_pack/__main__.py",
    "authored_pack/cli.py",
    "authored_pack/manifest.py",
    "authored_pack/pack.py",
    "authored_pack/safeio.py",
}
if not any(name.endswith(".dist-info/licenses/LICENSE") for name in wheel_names):
    raise SystemExit("wheel missing dist-info/licenses/LICENSE")
missing = required_wheel - wheel_names
if missing:
    raise SystemExit(f"wheel missing runtime files: {sorted(missing)}")

with tarfile.open(sdist_path, "r:gz") as tf:
    sdist_names = {member.name for member in tf.getmembers()}
    roots = {PurePosixPath(name).parts[0] for name in sdist_names if PurePosixPath(name).parts}
    if len(roots) != 1:
        raise SystemExit(f"sdist must have one top-level directory: {sorted(roots)}")
    root = next(iter(roots))
    required_sdist = {
        f"{root}/LICENSE",
        f"{root}/PKG-INFO",
        f"{root}/README.md",
        f"{root}/pyproject.toml",
        f"{root}/authored_pack/pack.py",
    }
    missing = required_sdist - sdist_names
    if missing:
        raise SystemExit(f"sdist missing runtime files: {sorted(missing)}")
    pkg_info_member = tf.extractfile(f"{root}/PKG-INFO")
    if pkg_info_member is None:
        raise SystemExit("sdist PKG-INFO is not a regular file")
    sdist_metadata_raw = pkg_info_member.read()

def validate_metadata(label, raw):
    metadata = BytesParser(policy=default).parsebytes(raw)
    expected = {
        "Name": "authored-pack",
        "Version": "0.2.5",
        "Requires-Python": ">=3.11",
        "License-Expression": "Apache-2.0",
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise SystemExit(f"{label} metadata {key} mismatch: {metadata.get(key)!r}")
    license_files = metadata.get_all("License-File", [])
    if not any(PurePosixPath(value).name == "LICENSE" for value in license_files):
        raise SystemExit(f"{label} metadata missing License-File: LICENSE")

validate_metadata("wheel", wheel_metadata_raw)
validate_metadata("sdist", sdist_metadata_raw)

forbidden_parts = {".git", ".github", ".control", ".claude", ".local_scratch", "tests", "scripts", "docs", "bin", "assets", "__pycache__"}
for label, names in (("wheel", wheel_names), ("sdist", sdist_names)):
    bad = []
    for name in names:
        parts = set(PurePosixPath(name).parts)
        if parts & forbidden_parts or name.endswith((".pyc", ".pyo")) or ".egg-info/" in name:
            bad.append(name)
    if bad:
        raise SystemExit(f"{label} contains forbidden generated or repo-only files: {sorted(bad)[:10]}")
print(f"wheel_files={len(wheel_names)}")
print(f"sdist_files={len(sdist_names)}")
PY

printf '\n==> rebuild wheel from sdist\n'
sdist_extract="$work_dir/sdist-source"
sdist_wheel_dir="$work_dir/sdist-wheel"
mkdir -p "$sdist_extract" "$sdist_wheel_dir"
tar -xzf "$sdist_path" -C "$sdist_extract"
sdist_source="$(find "$sdist_extract" -mindepth 1 -maxdepth 1 -type d -print -quit)"
"$python_bin" -m build --wheel --outdir "$sdist_wheel_dir" "$sdist_source"
sdist_wheel_path="$(find "$sdist_wheel_dir" -maxdepth 1 -type f -name '*.whl' -print -quit)"
[[ -n "$sdist_wheel_path" ]] || {
  echo "release_check: sdist did not rebuild a wheel" >&2
  exit 1
}

"$python_bin" - "$wheel_path" "$sdist_wheel_path" <<'PY'
import sys
import zipfile

def members(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}

left = members(sys.argv[1])
right = members(sys.argv[2])
if left != right:
    raise SystemExit("wheel rebuilt from sdist does not match wheel built from checkout")
print("sdist_wheel_matches=1")
PY

printf '\n==> installed console smoke outside checkout\n'
# Installed commands covered below: authored-pack --help, authored-pack assemble,
# authored-pack inspect, and authored-pack verify.
venv_dir="$work_dir/venv"
consumer_dir="$work_dir/consumer"
"$python_bin" -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --no-deps "$wheel_path" >/dev/null
mkdir -p "$consumer_dir/input" "$consumer_dir/out"
printf 'installed wheel smoke\n' > "$consumer_dir/input/note.txt"
(
  cd "$consumer_dir"
  "$venv_dir/bin/authored-pack" --help >/dev/null
  assemble_json="$("$venv_dir/bin/authored-pack" assemble --input "$consumer_dir/input" --out "$consumer_dir/out" --zip --json)"
  pack_dir="$(ASSEMBLE_JSON="$assemble_json" "$venv_dir/bin/python" -c 'import json, os; print(json.loads(os.environ["ASSEMBLE_JSON"])["result"]["pack_dir"])')"
  "$venv_dir/bin/authored-pack" inspect --pack "$pack_dir/authored_pack.zip" --json >/dev/null
  "$venv_dir/bin/authored-pack" verify --pack "$pack_dir/authored_pack.zip" --json >/dev/null
)

printf '\nrelease_check: ok\n'
