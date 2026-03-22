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

## 2026-03-22T15:33:25Z

- Documentation hardening wave for the public-release pass.
- What changed and why:
  - `README.md` now states the seed trust boundary plainly: published packs reproduce the same derived seed material, so `seed_master` should not be handed to untrusted agents as if it were secret.
    - Why: the old wording blurred operational secrecy with derived reproducibility and invited unsafe handoff patterns.
  - `README.md` now says pack directories are named by the root hash, not by `pack_id`.
    - Why: `pack_id` is metadata only; the directory name is content-addressed and must stay that way for operator predictability.
  - `ssot/ui/TUI_STANDARD_v0.1.0.md` is now the normative EPS baseline, and `ssot/ui/TUI_CONTRACT_v0.0.4.md` is explicitly historical/reference-only.
    - Why: there should be one clear conformance target; the older contract is useful context, not the release rule.
  - `docs/CROSS_AGENT_CONTROL_PLANE_PROMPT.md` now documents the JSON envelope and the conditional receipt fields.
    - Why: downstream automation needs exact shapes, not loose prose, if it is going to consume EPS outputs safely.
  - Added `docs/RELEASE_NOTES_v0.2.0.md` to pin the public-release contract in one place.
    - Why: this gives future Aaron a single artifact to consult when reconciling the schema bump, console script, and receipt/provenance rules.
- Assumptions this log entry depends on:
  - New stamps will emit `entropy.pack.v2` and `eps.receipt.v2`.
  - `eps` will be exposed as a console script in addition to `python -m eps`.
  - JSON mode will be stdout-only and stable on both success and expected operational failure paths.
- Verification note:
  - Documentation-only change; no runtime behavior was modified in this pass.

## 2026-03-22T18:12:00Z

- Public-release hardening wave landed for `v0.2.0`.
- What changed and why:
  - Added `eps/safeio.py` and routed trusted manifest/root reads through it.
    - Why: pathname-only reads are raceable; safe open/read is the minimum acceptable primitive for public verification software.
  - New stamps now emit `entropy.pack.v2` and `eps.receipt.v2`.
    - Why: derivation metadata and receipt consistency are now part of the public contract, so the schema needed a versioned cut.
  - Seed derivation metadata is now rooted into the manifest whenever derivation is enabled.
    - Why: provenance that affects operator interpretation must be bound to the content identity, not left mutable in `receipt.json` alone.
  - `entropy_pack.zip` is now built from finalized metadata and limited to the canonical public surface (`manifest.json`, `entropy_root_sha256.txt`, `receipt.json`, `payload/**`).
    - Why: zip and directory forms should tell the same public story; operational byproducts and local audit adjuncts do not belong in the public archive.
  - CLI `--json` mode now uses one envelope shape and no longer relies on human stderr for expected operational failures.
    - Why: automation needs a stable machine contract, not a mixture of JSON, stderr prose, and occasional tracebacks.
  - Entropy-bin recovery now preserves staged files under `.eps_failed/...` on failure.
    - Why: destructive entropy consumption without failure preservation is unacceptable; entropy loss must be noisy and reversible.
  - TUI seed display is now a one-shot viewer, and the TUI only labels mixed-source derivation as `LOCKDOWN`.
    - Why: the old UI could mislead operators and could leak raw seed material through persistent logs.
- Test/verification status after this wave:
  - `pytest -q` passed
  - `python3 -m pytest -q` passed
- Future-self note:
  - The main contract is now split cleanly: rooted facts in `manifest.json`, operational facts in `receipt.json`, and local-only audit adjuncts outside the canonical public zip.
  - If future work adds signatures or peppers, do it as a new versioned derivation/receipt contract. Do not smuggle new semantics into `v2`.

## 2026-03-22T20:02:45Z

- CLI and docs wording cleanup to match the actual semantics of EPS.
- What changed and why:
  - `eps/cli.py` now describes EPS as deterministic packaging and verification of operator-supplied entropy-bearing inputs, with reproducible derived seed material, rather than "entropy provenance tooling" that sounds like an RNG.
    - Why: the public help text should tell the honest story in one sentence and not invite misuse by implying EPS synthesizes randomness.
  - `README.md` now says EPS does not create entropy, renames the explanatory sections around "Why EPS Exists" and "Why Seven Inputs," and calls out the deterministic derived seed model explicitly.
    - Why: future Aaron should not have to infer the trust boundary from scattered prose; the README should make the packaging/verification model obvious.
  - `docs/RELEASE_NOTES_v0.2.0.md` now states the release as deterministic packaging and verification of operator-supplied entropy-bearing inputs, not an RNG.
    - Why: release notes are part of the contract and should not overclaim.
  - `docs/CROSS_AGENT_CONTROL_PLANE_PROMPT.md` now says the headless mode is deterministic packaging and verification, and warns that omitting `seed_master.*` from the public zip is not a secrecy control.
    - Why: downstream agents need the correct mental model before they consume EPS outputs.
- Verification status after this wording pass:
  - CLI contract tests were not rerun yet in this entry; the next check is `pytest -q tests/test_cli_contract.py tests/test_cli_binmode_guards.py`.
- Future-self note:
  - The naming drift risk here is semantic, not mechanical. If docs ever start implying EPS generates entropy, the product will be easier to misuse even if the code is correct.

## 2026-03-22T15:50:33Z

- Runtime hardening landed for the public-release `v0.2.0` pass.
- What changed and why:
  - Added `eps/safeio.py` and moved trusted local reads/hashing onto a single file-descriptor-safe path.
    - Why: pathname-only checks were leaving race windows around source hashing and canonical file reads.
  - `eps/manifest.py` now emits `entropy.pack.v2` for new stamps, and `eps/pack.py` verifies both `v1` and `v2` manifests.
    - Why: we needed rooted derivation metadata for public verification without breaking old packs.
  - Derivation provenance now lives in the rooted manifest and is mirrored into `eps.receipt.v2`.
    - Why: seed path claims in `receipt.json` were previously mutable and not tied to the pack identity.
  - `receipt.json` is finalized before `entropy_pack.zip` is written, and the public zip now contains only rooted metadata plus `payload/**`.
    - Why: zip and directory forms now present the same public provenance story.
  - The CLI now exposes a machine-readable JSON envelope for success and expected failures, and `pyproject.toml` now declares the `eps` console script.
    - Why: headless automation needs a stable contract; raw tracebacks are not a contract.
  - Bin-mode failure handling now preserves staged entropy under `.eps_failed/...` instead of risking silent loss.
    - Why: failure should be noisy and recoverable, never destructive.
  - The TUI no longer persists raw `seed_master` in log lines; it uses a one-shot reveal and writes audit completeness into `receipt.json`.
    - Why: persistent terminal logs are a real leak surface, and audit degradation needs to survive past the live session.
- Verification status after the runtime hardening wave:
  - `PYTHONDONTWRITEBYTECODE=1 pytest -q` passed (`52 passed`)
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q` passed (`52 passed`)
  - `python3 -m eps --help` passed
  - `git diff --check` passed
- Operational insight for future me:
  - The risky seams in EPS are still the cross-module contracts: manifest schema, receipt schema, and TUI orchestration. Change them together or not at all.
  - The fastest way to break public trust here is to let docs drift away from the actual JSON and zip contracts. Treat the tests as the executable version of the release notes.

## 2026-03-22T20:27:08Z

- Public artifact lifecycle and identity cleanup landed after the initial v0.2.0 hardening wave.
- What changed and why:
  - `eps/pack.py` now uses one authoritative finalize path for `receipt.json`, `entropy_pack.zip`, and the evidence bundle.
    - Why: the previous flow could leave zip and evidence artifacts carrying stale receipt state relative to the on-disk pack directory.
  - Evidence bundle metadata is no longer written back into `receipt.json`.
    - Why: receipt -> evidence -> receipt was a cycle. Breaking that cycle is the simplest way to make the evidence bundle reflect final pack state.
  - New stamps now emit both `pack_root_sha256.txt` and legacy alias `entropy_root_sha256.txt`, and receipts/results now expose both `pack_root_sha256` and `payload_root_sha256`.
    - Why: one hash was carrying too many meanings. Future-Aaron needs a clean split between the full pack commitment and payload equivalence.
  - The TUI now feeds source-audit fields into the core finalization path instead of patching `receipt.json` after publication.
    - Why: the TUI was the easiest place for zip/receipt drift to recur if finalization stayed split across layers.
  - CLI and README wording now prefer `pack_root_sha256`, `payload_root_sha256`, and "derived seed material" while keeping legacy aliases for compatibility.
    - Why: the semantics need to read honestly without forcing an immediate flag-day on old automation.
- Verification status after this cleanup wave:
  - Focused pack/CLI/TUI regression suite passed after the contract updates.
- Future-self note:
  - If you change receipt fields, zip contents, or evidence composition, treat them as one seam. Splitting them again will reintroduce stale-artifact bugs immediately.

## 2026-03-22T21:05:00Z

- Public `v1.0.0` repo-prep pass landed on the release branch candidate.
- What changed and why:
  - Bumped `eps/__init__.py` and `pyproject.toml` from `0.2.0` to `1.0.0`.
    - Why: public release prep needs one visible version across runtime and package metadata.
  - `README.md` now carries an explicit V1 contract, public-scope note, and source-available/non-OSI wording.
    - Why: future Aaron should not have to reconstruct the product boundary from scattered notes when the repo goes public.
  - Added `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, and `.github/workflows/ci.yml`.
    - Why: a public repo without contribution, disclosure, and CI rails invites drift immediately.
  - Removed tracked `.claude/settings.local.json` from git and added `.claude/` to `.gitignore`.
    - Why: local tooling state does not belong in the public contract.
  - Added `docs/RELEASE_NOTES_v1.0.0.md` and marked `docs/SEALED_PACK_ARCHITECTURE.md` as future design only, not implemented in V1.
    - Why: public deterministic EPS needs a clean boundary before sealed-mode work starts.
  - Scrubbed workspace-specific absolute paths from public-facing docs like `docs/CROSS_AGENT_CONTROL_PLANE_PROMPT.md` and `docs/CHATGPT_PRO_REDTEAM_ENTROPY_DOSSIER.md`.
    - Why: publishing a repo should not leak local workstation assumptions.
  - Added `tests/test_public_release_contract.py`.
    - Why: version sync, README boundary language, CI presence, and local-ignore rules should be executable checks, not memory.
- Verification status after this prep pass:
  - pending full gate rerun in the next step: `pytest -q`, `python3 -m pytest -q`, `python3 -m eps --help`, and one real temp-dir stamp/verify smoke run.
- Future-self note:
  - The public-release failure mode here is mostly semantic drift. If version, docs, CI, and trust-boundary language stop matching the runtime, the repo will rot even if the core hashing logic stays correct.
