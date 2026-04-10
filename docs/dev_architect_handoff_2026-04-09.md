# Authored Pack Dev Architect Handoff — 2026-04-09

Maintainer note: this is repo-local handoff context for future improvement sessions, not part of the current public product contract.

This is a durable repo-local handoff for the current `authored-pack` improvement slice.

Its purpose is simple:

- capture what changed
- capture what was actually verified
- preserve the open questions worth architect attention
- give future Dev Architect sessions a ready prompt without Aaron re-pasting context

## Ground Truth As Of 2026-04-09

This handoff reflects the repo state on 2026-04-09 in a dirty worktree on branch:

`codex/dropzone-pinball-sfx`

Relevant improvement areas already landed in the worktree:

- TUI Sources screen now collapses the old visible `A add photo` path into `P import`
- public release/demo/docs surface now leads with `assemble` instead of `stamp`
- CLI agent behavior was hardened around exit codes, help shape, and `consume-bin` path defaults
- README install wording now prefers run-from-clone first and treats installed CLI use as optional
- CI now points at the canonical `bash scripts/release_check.sh` gate

## What Changed

### 1. TUI Human Surface

Sources-stage copy was simplified so the operator sees three distinct actions instead of overlapping staging verbs:

- `T` = type note text
- `Space` = tap keys
- `P` = import files/folders

The visible `A add photo` action was removed from the Sources screen copy because it read like a duplicate of import.

Primary files:

- `bin/authored_pack.py`
- `tests/test_tui_experience_contract.py`

### 2. Public Surface Drift

The public-facing repo surface now reinforces the intended verb set:

- `assemble`
- `verify`
- `inspect`
- `consume-bin`

Demo/install/public-copy assets that still led with `stamp` were shifted to `assemble`.

Primary files:

- `scripts/demo_v1.sh`
- `scripts/smoke_install.sh`
- `docs/CANONICAL_DEMO.md`
- `docs/PUBLIC_COPY_ASSETS.md`
- `tests/test_public_release_contract.py`

### 3. CLI Hardening For Agents

The CLI was tightened so automation and agents get clearer machine behavior:

- bare invocation now exits non-zero instead of looking successful
- JSON usage/validation failures preserve usage-style exit code `2`
- `consume-bin` defaults are anchored to repo-root bins instead of caller cwd
- help usage now foregrounds primary verbs while keeping compatibility aliases visible
- human `consume-bin` output includes emitted artifact paths when present

Primary files:

- `authored_pack/cli.py`
- `tests/test_cli_contract.py`
- `tests/test_cli_binmode_guards.py`

## What Was Verified

These checks were run and passed during this slice:

```bash
pytest -q tests/test_tui_experience_contract.py tests/test_tui_p1_regressions.py tests/test_tui_audit_quick_wins.py
python3 scripts/smoke_tui_pty.py
pytest -q tests/test_cli_contract.py tests/test_cli_binmode_guards.py tests/test_public_release_contract.py
python3 -m authored_pack --help
bash scripts/release_check.sh
```

Observed note:

- `scripts/smoke_install.sh` remains at a historical path name, but the current proof is repo-local: it runs `python3 -m authored_pack`, parses the JSON envelope, and consumes the returned object without any `pip` install step

## Remaining Questions Worth Architect Attention

### P1

- Decide whether the historical script path `scripts/smoke_install.sh` should be renamed to better match its current repo-local consumer-smoke role.
- Confirm whether public docs should continue to keep installed-CLI packaging out of the primary release contract unless a separate install lane is explicitly proven.

### P2

- Consider adding richer JSON `details` for generic `ValueError` CLI failures so agent consumers get more structured failure envelopes.
- Decide whether a later release should introduce a separately named installed-CLI proof lane or keep packaging claims intentionally modest.

## Do Not Reopen Without Cause

- Do not rename schemas.
- Do not remove `stamp` or `stamp-bin` compatibility aliases unless explicitly directed.
- Do not widen this repo toward governed attestation, sealed mode, or broader evidence-runtime claims.
- Do not reintroduce overlapping TUI verbs unless there is a concrete operator need.

## Dev Architect Prompt

Use this as the default handoff prompt for the next architect-quality review or follow-on slice:

```text
Repo: /Users/aaronday/dev/authored-pack

Read first:
- AGENTS.md
- README.md
- CONTRIBUTING.md
- docs/authored_pack_plan_2026-03-30.md
- docs/repo_architect_handoff_2026-03-30.md
- docs/dev_architect_handoff_2026-04-09.md

Mode: review or execute, whichever is justified by ground truth.

Intent:
Inspect the current authored-pack improvement slice and either:
1. confirm that the new TUI/public-surface/CLI changes are coherent and shipworthy, or
2. make one bounded high-leverage follow-on improvement without widening scope.

Priority focus:
- release-surface honesty
- agent-usable CLI behavior
- deterministic pack/verify positioning
- keeping public `assemble` / `consume-bin` language aligned across docs, help, demos, and smoke checks

Allowed files unless verification forces adjacent edits:
- README.md
- authored_pack/cli.py
- bin/authored_pack.py
- scripts/
- .github/workflows/ci.yml
- tests/test_public_release_contract.py
- tests/test_cli_contract.py
- tests/test_cli_binmode_guards.py
- tests/test_tui_experience_contract.py
- tests/test_tui_p1_regressions.py
- tests/test_tui_audit_quick_wins.py

Required behavior:
- keep changes small and reversible
- preserve compatibility aliases unless explicitly changing them
- do not widen into attestation/runtime claims
- do not mix unrelated lanes in one diff

Verification:
- TUI-only: pytest -q tests/test_tui_experience_contract.py tests/test_tui_p1_regressions.py tests/test_tui_audit_quick_wins.py && python3 scripts/smoke_tui_pty.py
- public/CLI: pytest -q tests/test_cli_contract.py tests/test_cli_binmode_guards.py tests/test_public_release_contract.py && python3 -m authored_pack --help
- release-sensitive: bash scripts/release_check.sh

Report:
- exact ground truth inspected
- findings ordered by P0/P1/P2
- smallest durable fix
- what was verified
- what remains unknown
```

## Update Rule

If a future slice materially improves the repo, do not rely on chat history.

Create a new dated handoff doc or update this one, then make sure `AGENTS.md` points future sessions at the latest durable handoff.
