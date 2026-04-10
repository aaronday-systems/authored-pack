#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_step() {
  local label="$1"
  shift
  printf '\n==> %s\n' "$label"
  (
    cd "$ROOT"
    "$@"
  )
}

run_step "pytest" pytest -q
run_step "cli help" python3 -m authored_pack --help
run_step "tui pty smoke" python3 scripts/smoke_tui_pty.py
run_step "repo cli consumer smoke" bash scripts/smoke_install.sh
run_step "demo smoke" bash scripts/demo_v1.sh

printf '\nrelease_check: ok\n'
