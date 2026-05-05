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
- The CI release-check step sets `PYTHON_BIN=python` so `scripts/release_check.sh` uses the matrix interpreter instead of auto-selecting another installed runner Python.

Why it matters
- The release gate was valid locally but under-specified in CI; remote public-build status could stay red without exercising the repo tests.
- The Python 3.11 job can otherwise install dependencies into 3.11 and then accidentally run the release gate under 3.12.

Verification
- Pending: rerun `bash scripts/release_check.sh` on a clean committed tree, push, then confirm GitHub Actions is green for `origin/main`.

## 2026-05-02 - Repo-local Control Plane lane

Status
- Added `.control/lane.json` so Control Plane repo-watch can select Authored Pack from a repo-owned typed contract.

What changed
- Declared the `authored-pack.release-contract` lane with local Codex dispatch, release-contract scope, and `bash scripts/release_check.sh` as the verification command.

Why it matters
- Authored Pack can be pulled into the owned control loop without becoming an attestation engine or orchestration layer.

Key tradeoff
- The lane is deliberately bounded to deterministic assemble/verify and public-surface gaps.

Verification
- `python3 -m json.tool .control/lane.json` passed.
- Control Plane `bin/helmuth-repo-watch once --skip-helmuth --skip-checks --json` accepted the lane in `control-plane.repo_local_queue.v1`.

Remaining uncertainty
- No lane execution receipt has been written for this new lane instance yet.

## 2026-05-03 - Release-contract lane closeout receipt

Status
- Closed `authored-pack.release-contract.20260502` with a repo-local structured task receipt.

What changed
- Added `docs/task_receipts/authored-pack_20260503T151113Z.json` and `.md` with a passing `bash scripts/release_check.sh` verification entry for the lane's selected verify command.

Why it matters
- Control Plane closes repo-local lanes from typed receipts, not prose success claims. The release-contract lane now has the structured evidence it required.

Key tradeoff
- The active worktree already held uncommitted lane/log artifacts, so `scripts/release_check.sh` correctly refused to run there. The canonical gate was run from a detached clean worktree at HEAD instead of weakening the clean-tree preflight.

Verification
- `python3 -m json.tool .control/lane.json` passed.
- `bash scripts/release_check.sh` in the active worktree failed only at the expected clean-tracked-tree preflight.
- `bash scripts/release_check.sh` passed in `/private/tmp/authored-pack-release-check-20260503T151028Z`.
- Control Plane selected `authored-pack.release-contract` before the receipt was written.
- `python3 -m json.tool docs/task_receipts/authored-pack_20260503T151113Z.json` passed.
- Control Plane reported `no_queueable_lane` after the receipt was written, proving the lane instance is covered.

Remaining uncertainty
- None for this lane closeout.

## 2026-05-05 - Control Plane execution claim command

Status
- Added the smallest CLI slice for recording execution intent without using the current repo as authority.

What changed
- `authored-pack claim-execution` records mode, objective, Control Plane authority, and the invoking directory as `repo_hint`.
- The command writes `claim.json` and `receipt.json` under an explicit Control Plane state root outside the invoking cwd by default.
- Repo-local control roots are rejected so path gravity cannot silently become execution authority.

Why it matters
- Aaron can invoke one command from any repo and leave a typed claim/receipt for Control Plane instead of becoming the command runner or treating repository location as approval.

Verification
- `pytest -q tests/test_cli_contract.py tests/test_public_release_contract.py` passed.
- `python3 -m authored_pack --help` passed.
- A temp editable install in `/private/tmp/authored-pack-claim-venv` ran `authored-pack claim-execution --mode execute --objective 'smallest non-servo slice smoke' --control-root /private/tmp/authored-pack-control-claim-smoke --json` from `/private/tmp/authored-pack-other-repo` and wrote both claim and receipt.

Remaining uncertainty
- This records claims only; Control Plane still needs to consume these claim files for scheduling or execution.

## 2026-05-05 - Detached release-contract closeout helper

Status
- Added a repo-local helper for closing `authored-pack.release-contract` when the active tracked tree is dirty.

What changed
- `scripts/close_release_contract.py` reads `.control/lane.json`, creates a detached clean worktree at `HEAD`, runs `bash scripts/release_check.sh` there, removes the worktree, and writes a matching `dev-architect.task_receipt.v1` JSON receipt under `docs/task_receipts/`.
- `tests/test_public_release_contract.py` now locks the helper to the detached-worktree release path and guards against stash/reset-style dirty-tree workarounds.
- Wrote `docs/task_receipts/authored-pack_20260505T162423Z.json` from the helper.

Why it matters
- The release gate remains strict about clean tracked worktrees, while dirty active-tree closeout work can still leave typed lane evidence without turning Aaron into the command runner.

Key tradeoff
- The detached release gate verifies committed `HEAD`; uncommitted active-tree edits still need their own narrow tests or a later clean-tree release check after commit.

Verification
- `pytest -q tests/test_public_release_contract.py` passed.
- `python3 scripts/close_release_contract.py --json` passed after sandbox approval for detached worktree creation and wrote the receipt.
- `python3 -m json.tool docs/task_receipts/authored-pack_20260505T162423Z.json` passed.
- The helper's detached worktree ran `bash scripts/release_check.sh` with exit code 0.

Remaining uncertainty
- The full release gate has not run against these uncommitted helper/test/log edits; by design, it can only do that after they are committed or checked from a clean worktree containing them.
