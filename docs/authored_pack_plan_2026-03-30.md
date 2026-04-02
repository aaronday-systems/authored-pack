# Authored Pack Plan — 2026-03-30

This is the working plan for `authored-pack` based on the current ontology decision.

## Locked Rules

- `Authored Pack` keeps its native schemas:
  - `authored.pack.v1`
  - `authored.receipt.v1`
  - optional `authored.evidence.v1`
- `Attestation Engine` keeps its native schemas:
  - `ExecutionEvidenceBundle_v1`
  - `VerificationResult_v1`
  - `PaymentEvidence_v1`
- `Evidence Pack` is a share/export container term, not a native canonical schema name.
- `EvidencePack_v1` is deferred until there are at least:
  - 2 real producers
  - 1 real consumer
  - real exchange pressure

## Goal

Keep `Authored Pack` public and alive as an honest deterministic assembly tool.

Do not let it drift into:
- a disguised `Attestation Engine`
- a generic evidence ontology repo
- a second flagship proof product

## What We Are Actually Shipping

`Authored Pack` should read as:

> a small deterministic tool for assembling, verifying, inspecting, and exporting bounded artifact sets

It should **not** read as:

> governed attestation
> proof of what happened
> universal evidence runtime

## Plan

### Phase 1 — Reframe the front door

Targets:
- `/Users/aaronday/dev/authored-pack/README.md`

Changes:
- tighten the first 15 lines around:
  - what this is
  - what it is not
  - what state it is in
  - what to run next
- explicitly state that this is a deterministic pack/verify tool
- explicitly state that governed attestation belongs to `Attestation Engine`
- remove any wording that makes `Authored Pack` sound like the broader provenance runtime

Acceptance:
- a stranger can understand the repo without knowing the broader stack
- the repo remains interesting
- the proof boundary is explicit

### Phase 2 — Fix the verbs

Targets:
- `/Users/aaronday/dev/authored-pack/README.md`
- `/Users/aaronday/dev/authored-pack/authored_pack/cli.py`
- `/Users/aaronday/dev/authored-pack/bin/authored_pack.py`

Primary user-facing verbs:
- `assemble`
- `verify`
- `inspect`
- `export`

Machine/disposable-bin verb:
- `consume-bin`

Compatibility verbs to keep temporarily:
- `stamp`
- `stamp-bin`

Changes:
- present `assemble` as the primary create verb in README and CLI help
- keep `stamp` as an alias/compatibility command
- relabel `stamp-bin` publicly as `consume-bin`
- keep `stamp-bin` as an alias if needed
- demote `seal`, `sealed`, and `prove` from primary copy

Acceptance:
- README and help text no longer lead with `stamp`
- subtractive bin behavior is described honestly
- compatibility is preserved

### Phase 3 — Tighten TUI/operator language

Targets:
- `/Users/aaronday/dev/authored-pack/bin/authored_pack.py`

Changes:
- replace visible `Stamp` labels with `Assemble`
- replace visible `stamp-bin` labels with `Consume Bin` or equivalent
- keep internal function names alone unless there is a low-risk alias path
- make the TUI describe the action as assembling a deterministic pack, not proving or sealing anything

Acceptance:
- operator language matches the public README language
- the repo feels cleaner without losing energy

### Phase 4 — Preserve schema stability

Targets:
- only touch schema/version names if strictly necessary

Rules:
- do not rename `authored.pack.v1`
- do not rename `authored.receipt.v1`
- do not rename `authored.evidence.v1`
- do not introduce `EvidencePack_v1` here

Acceptance:
- no schema churn
- no forced compatibility work
- no fake unification

## Deferred On Purpose

Do **not** do any of this in the current slice:

- define `EvidencePack_v1`
- rename native schemas to `Evidence Pack`
- collapse `Authored Pack` into `Attestation Engine`
- make the repo carry governed provenance claims
- build a large interop abstraction for hypothetical future producers

## Failure Mode To Watch

If someone proposes:

> “Let’s just define the universal evidence schema now so everything can conform later.”

Call it out explicitly:

**This smells like LCD lock-in.**

Meaning:
- lowest-common-denominator lock-in
- premature unification
- wrong shared layer frozen too early

## Exact File Order

1. `/Users/aaronday/dev/authored-pack/README.md`
2. `/Users/aaronday/dev/authored-pack/authored_pack/cli.py`
3. `/Users/aaronday/dev/authored-pack/bin/authored_pack.py`

## Done When

- `Authored Pack` reads as a strong public honest tool
- the verb set is cleaner than `stamp`
- the repo no longer sounds like a disguised attestation runtime
- native schemas remain untouched
- `Evidence Pack` remains deferred as a future export/interchange question, not a present native schema
