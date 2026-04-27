# Execution Log

Append-only historical log for material repo changes and decisions.

## 2026-04-22 — Durable decision logging baseline

Status
- Established `docs/execution_log.md` as the canonical historical log for material work done through this application.

What changed
- Updated `AGENTS.md` so material code, config, schema, ops, security, runtime-policy, and workflow changes leave a durable note.
- Locked the minimum note shape: what changed, why, key tradeoff, verification actually performed, and what remains uncertain.

Why it matters
- Future LLM passes need recoverable decisions, not just diffs or chat residue.
- The repo now has a stable place to learn from prior changes without replaying the whole thread.

Verification
- Updated `AGENTS.md`.

## 2026-04-27 — Release-blocking verification and routing fixes

Status
- Closed three review findings before public website traffic: zip closure, reuse-time seed file materialization, and TUI drop-routing label coupling.

What changed
- `verify_pack()` now rejects unknown non-payload members in pack zips while preserving legacy `entropy.pack.v1` compatibility through an explicit allowlist.
- Idempotent `assemble_pack()` reuse now writes requested `seed_master.hex` and `seed_master.b64` files with private mode when derived seed material exists.
- TUI drop routing now uses stable screen keys instead of visible menu labels.

Why it matters
- A verified public zip should not be able to carry unverified top-level files.
- Reuse behavior should honor the same local artifact requests as fresh assembly.
- Operator copy can change without silently changing import/drop behavior.

Verification
- `pytest -q tests/test_stamp_verify.py tests/test_pack_hardening.py tests/test_tui_audit_quick_wins.py tests/test_tui_experience_contract.py tests/test_tui_p1_regressions.py tests/test_tui_header_contract.py`
- `python3 scripts/smoke_tui_pty.py`
- `pytest -q`
- `python3 -m authored_pack --help`
- `python3 -m authored_pack inspect --help`
- `bash scripts/release_check.sh` is the clean-tree push gate and must run after committing this dirty worktree.

## 2026-04-27 — CI installs test dependency before release gate

Status
- Fixed the public GitHub Actions build setup after the pushed release-fix commit failed before tests with `No module named pytest`.

What changed
- `.github/workflows/ci.yml` now installs `pytest` before running `scripts/release_check.sh` on Python 3.11 and 3.12.

Why it matters
- The release gate was valid locally but under-specified in CI; remote public-build status could stay red without exercising the repo tests.

Verification
- Pending: rerun `bash scripts/release_check.sh` on a clean committed tree, push, then confirm GitHub Actions is green for `origin/main`.
