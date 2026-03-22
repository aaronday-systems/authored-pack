# Clear Deck Plan

Date: 2026-03-22
Status: public-mode lifecycle, semantics, and split-identity cleanup landed; sealed mode still pending
Scope: remove semantic and lifecycle ambiguity so EPS can move into the next coding phase cleanly

## 1. Current Local State To Reconcile First

These local changes existed at the start of the cleanup pass:
- `bin/eps.py`
- `eps/cli.py`
- `docs/CHATGPT_PRO_REDTEAM_ENTROPY_DOSSIER.md`
- `tests/test_tui_experience_contract.py`

Reconciliation result:
- keep and integrate:
  - `bin/eps.py`
  - `eps/cli.py`
  - `tests/test_tui_experience_contract.py`
- keep as planning/reference docs:
  - `docs/CHATGPT_PRO_REDTEAM_ENTROPY_DOSSIER.md`

This mattered because those files overlapped the exact product surfaces the cleanup pass needed to change.

## 2. Public-Mode Cleanup Order

### A. Fix lifecycle coherence

Goal:
- one final receipt
- one final public zip
- one non-cyclic evidence model

Required changes:
1. eliminate post-zip receipt mutation
2. eliminate post-bundle receipt mutation
3. redesign the evidence-bundle reference so receipt and bundle do not depend on each other cyclically

Definition of done:
- directory receipt and zipped receipt are byte-identical where both are present
- evidence bundle is derived from final pack state, not a stale intermediate state

Status:
- done in the current branch

### B. Fix semantics and naming

Goal:
- stop implying RNG behavior or secrecy where none exists

Required changes:
1. replace top-level "entropy generation" language with "operator-supplied secret material" language
2. rename or alias the public root away from `entropy_root_sha256`
3. rename or alias `seed_master` toward `derived_seed`
4. make mixed-source mode read as derivation context, not secret injection

Definition of done:
- README, CLI help, TUI labels, and release notes all tell the same story

Status:
- done in the current branch, with legacy aliases retained for compatibility

### C. Split identity classes

Goal:
- avoid one hash meaning too many things

Required changes:
1. define payload/content root
2. define pack/manifest root
3. document when each should be used by agents and operators

Definition of done:
- downstream systems can choose the right identity for reproducibility vs content equivalence

Status:
- done in the current branch via `pack_root_sha256` and `payload_root_sha256`

## 3. Sealed-Mode Prep Order

### A. Freeze the object model

Do not start coding sealed mode until these are locked:
1. outer public seal layout
2. inner encrypted envelope layout
3. signature boundary
4. break-glass access receipt format
5. statement of what is and is not provable offline

Primary reference:
- `docs/SEALED_PACK_ARCHITECTURE.md`

### B. Choose cryptographic implementation family

Need one explicit decision for:
1. recipient encryption
2. signature scheme
3. optional witness integration

Do not mix implementation experiments into the public-mode cleanup.

### C. Add test scaffolding before feature work

Needed first:
1. lifecycle invariants for public mode
2. fixture model for sealed outer/inner envelopes
3. signature and decryption failure-path tests

## 4. Recommended Coding Sequence

1. Reconcile the current uncommitted local edits. Done.
2. Fix public-mode lifecycle bugs. Done.
3. Align naming and docs with the real semantics. Done.
4. Add split-root identity model. Done.
5. Only then start sealed-pack implementation. Pending.

This order matters.
If sealed mode is started before lifecycle and naming are cleaned up, EPS will accumulate two overlapping trust models at once.

## 5. Immediate Next Coding Ticket

**Ticket:** Public artifact finalization pipeline

Implement one authoritative finalize path for current public packs:
- build final receipt exactly once
- write final receipt exactly once
- build public zip from final receipt state
- redesign evidence-bundle references so there is no receipt/bundle cycle

Status:
- executed in the current branch

Next coding start:
- sealed-pack implementation, using `docs/SEALED_PACK_ARCHITECTURE.md` as the frozen design boundary
