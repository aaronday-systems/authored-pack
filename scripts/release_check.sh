#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

resolve_python_bin() {
  local candidate
  for candidate in "${PYTHON_BIN:-}" python3.13 python3.12 python3.11 python3; do
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
  echo "release_check: python3.11+ is required" >&2
  return 1
}

require_clean_tracked_tree() {
  (
    cd "$ROOT"
    if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
      echo "release_check: tracked worktree must be clean before release verification" >&2
      git status --short --branch >&2
      exit 1
    fi
  )
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

run_step "clean tracked tree" require_clean_tracked_tree
run_step "pytest" "$python_bin" -m pytest -q
run_step "cli help" "$python_bin" -m authored_pack --help
run_step "tui pty smoke" "$python_bin" scripts/smoke_tui_pty.py
run_step "repo cli consumer smoke" env PYTHON_BIN="$python_bin" bash scripts/smoke_install.sh
run_step "demo smoke" env PYTHON_BIN="$python_bin" bash scripts/demo_v1.sh

printf '\nrelease_check: ok\n'
