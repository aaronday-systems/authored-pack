# Authored Pack Repo Contract

Authored Pack is a small deterministic pack/verify tool for bounded artifact sets.

Read these first before making changes:
- `README.md`
- `CONTRIBUTING.md`
- `docs/authored_pack_plan_2026-03-30.md`
- `docs/repo_architect_handoff_2026-03-30.md`

## Core Invariants

- Keep Authored Pack positioned as a deterministic assemble/verify tool, not an attestation engine, proof product, RNG, secrecy mechanism, or signed-provenance system.
- Preserve schema names:
  - `authored.pack.v1`
  - `authored.receipt.v1`
  - optional `authored.evidence.v1`
- Public create verb is `assemble`. `stamp` remains a compatibility alias unless explicitly changed.
- Public subtractive-bin verb is `consume-bin`. `stamp-bin` remains a compatibility alias unless explicitly changed.
- Do not widen scope into sealed mode, `EvidencePack_v1`, universal evidence ontology work, or Attestation Engine behavior.
- Keep changes small, explicit, reversible, and easy to verify.

## Allowed Scope By Work Mode

- TUI polish:
  - `bin/authored_pack.py`
  - `tests/test_tui_experience_contract.py`
  - `tests/test_tui_p1_regressions.py`
  - `tests/test_tui_audit_quick_wins.py`
- Public surface / wording:
  - `README.md`
  - `authored_pack/cli.py`
  - `bin/authored_pack.py`
  - `tests/test_public_release_contract.py`
- Release hardening / automation:
  - `scripts/`
  - `.github/workflows/ci.yml`
  - `CONTRIBUTING.md`
  - `tests/test_public_release_contract.py`
- Core pack/verify logic:
  - `authored_pack/pack.py`
  - `authored_pack/cli.py`
  - `tests/test_stamp_verify.py`
  - `tests/test_pack_hardening.py`
  - related contract tests

If a task needs files outside the smallest matching scope, explain why before widening the edit set.

## Required Verification

- Always run the narrowest relevant checks first.
- Minimum for TUI-only changes:
  - `pytest -q tests/test_tui_experience_contract.py tests/test_tui_p1_regressions.py tests/test_tui_audit_quick_wins.py`
- Add this when changing live TUI behavior, prompts, review flow, verify flow, or key bindings:
  - `python3 scripts/smoke_tui_pty.py`
- Minimum for public-surface or CLI-help changes:
  - `pytest -q tests/test_public_release_contract.py`
  - `python3 -m authored_pack --help`
- Minimum for release-sensitive changes:
  - `bash scripts/release_check.sh`

If checks are not run, say exactly which were skipped and why.

## Stop Conditions

- Stop and ask before renaming schemas, removing compatibility aliases, changing trust-boundary claims, or widening the repo toward attestation, sealed, or proof language.
- Stop if the worktree contains unrelated conflicting edits in files you need to touch.
- Stop if the task would require destructive or stateful automation on `consume-bin`, seed material, or pack contents without explicit approval.
- Stop if you cannot state the exact verification gate for the change.

## Subagents / Delegation

- Use subagents only as bounded read-only sidecars for specific lenses or file questions.
- Do not make subagents the primary coordination surface or the holder of critical-path state.
- Main thread owns critical-path execution, integration, and final synthesis.
- If a sidecar fails or times out, continue locally and label missing evidence explicitly.

## Execution Style

- Prefer encoding recurring judgment into tests, scripts, prompts, or repo conventions.
- Prefer one canonical release-check script over manual command chains.
- Prefer updating repo-local instructions over re-pasting the same handover into session titles.
