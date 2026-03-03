# Execution Log (Append-Only)

This file is an append-only chronological ledger of architecture and execution decisions.
Do not rewrite, reorder, or prune previous entries.

## 2026-02-28T08:57:09Z

- Initialized canonical historical log for `entropy-pack-stamper` to keep cross-repo state legible.
- Cross-repo alignment checkpoint recorded:
  - Active governed image demo orchestration is in `control-plane`.
  - Proof/evidence and attestation core remains in RXTX.
  - EPS remains an entropy stamping/verifiable pack utility, not a policy/orchestration authority.
- This entry exists so repo-local history reflects current system-wide demo posture.

## 2026-02-28T09:03:47Z

- Canonical historical log filename standardized to `docs/DEVLOG.md` for cross-repo consistency.
- Previous filename `docs/execution_log.md` is retired; historical entries are unchanged.

## 2026-03-03T20:07:27Z

- Refactor/hardening wave completed and pushed on `codex/dropzone-pinball-sfx`.
- Commit trace for future audits:
  - `e3ca3af` `Refactor verify: share dir/zip artifact validation core`
  - `e1355b0` `Test verify parity: dir and zip emit identical ordered errors`
  - `d4552a6` `TUI lockdown guards: enforce eligible unique sources and fail-fast materialization`
  - `200b48c` `Pack hardening: unique temp staging and streamed evidence bundle hashing`
- What changed and why:
  - `eps/pack.py` verify path was deduped into a shared artifact-validation core.
    - Why: keep dir/zip verify behavior coupled by construction; reduce future drift risk and simplify security fixes.
  - Added parity regression test ensuring dir/zip error list ordering/messages stay identical for the same malformed pack.
    - Why: if refactors diverge error semantics, this test catches it immediately.
  - Lockdown seed-mixing gate in TUI now uses **eligible unique** source count (not raw source list length).
    - Why: raw count can be gamed by duplicates and low-quality captures; this enforces stronger operator intent.
  - Tap entropy now rejects low-event captures (`LOCKDOWN_MIN_TAP_EVENTS=16`) instead of silently adding weak sources.
    - Why: avoid false confidence where "minimum sources reached" is satisfied by near-empty tap captures.
  - `@sources` payload materialization now fails fast on missing/unreadable source files.
    - Why: silent partial success is dangerous; stamping must not proceed with quietly dropped inputs.
  - Evidence bundle sidecar hash now streams zip bytes instead of `read_bytes()` into memory.
    - Why: remove avoidable memory spikes on large bundles.
  - Temp pack staging now uses `tempfile.mkdtemp(..., dir=out_dir)` instead of pid+timestamp naming.
    - Why: avoid rare collision/race windows under rapid/concurrent runs.
- Verification status after this wave:
  - `pytest -q` passed (`20 passed`)
  - `python3 -m pytest -q` passed (`20 passed`)
- Operational insight for future me:
  - The highest-leverage pattern in this repo is to encode invariants as tests immediately after refactors.
  - The most fragile surface is still `bin/eps.py` (UI + orchestration in one file); future safety work should keep extracting pure logic out of curses paths.
