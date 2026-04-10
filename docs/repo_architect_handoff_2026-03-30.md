# Authored Pack Repo Architect Handoff — 2026-03-30

Maintainer note: this is an internal architect handoff, not part of the current public product contract.

This is a positioning and language handoff for `authored-pack`.

It is not a request to kill the repo.
It is not a request to flatten it into `Attestation Engine`.
It is a request to keep the repo public, sharp, and honest without letting it drift into the wrong category.

## Core Decision

`Authored Pack` should remain a real public tool.

But it should **not** be positioned as:
- the main attestation product
- the canonical provenance runtime
- a synonym for `Attestation Engine`
- a synonym for `Evidence Pack`

The honest role is:

**Authored Pack is a small deterministic pack/verify tool for bounded artifact sets.**

That is interesting enough.
That is public enough.
That is true enough.

## Why This Matters

Right now there is a category-collision risk:

- `Authored Pack` is a deterministic assembly/verification tool
- `Attestation Engine` is a governed attestation/runtime system
- older cross-repo language used `Evidence Pack` as a transport/container term around canonical proof artifacts

If `Authored Pack` gets framed as the proof product, it starts carrying semantic weight it does not actually hold.

That would make the repo less honest, not more ambitious.

## The Right Public Position

Use a thesis this small:

> Authored Pack is a deterministic pack/verify tool for humans and agents who want to assemble, inspect, and re-check a bounded artifact set.

Use a non-goal this sharp:

> It does not prove governed execution provenance, world-state truth, timestamp truth, or signed attestation.

Use a relation to `Attestation Engine` this narrow:

> Authored Pack can feed stronger attestation workflows later, but it is useful on its own without pretending to be the runtime that provides governed attestation.

## Product/Category Guidance

### Keep

- public honest tool
- deterministic assembly
- deterministic hashing
- manifest + receipt + root identity
- inspectable local artifact packs
- human/manual or agent-assisted staging

### Do Not Claim

- governed execution evidence
- detached verification of governed runs
- proof of what happened in the world
- non-bypassability
- timeline truth
- signed attestation unless a real signing surface exists

### Strategic Position

Treat `Authored Pack` as:
- a supporting deterministic tool
- a manual lane
- a public wedge
- a first-legible repo

Do **not** treat it as:
- a second flagship beside `Attestation Engine`
- the same proof category as `Attestation Engine`
- the canonical evidence ontology for the broader stack

## Recommended Nouns

### Keep These

- `Authored Pack`
  - the tool/repo name

- `pack`
  - the primary artifact noun inside this repo

- `receipt`
  - the pack receipt

- `pack root`
  - stable pack identity

- `payload root`
  - payload-only identity

- `pack directory`
  - unpacked emitted artifact

- `pack zip`
  - zipped emitted artifact

### Use Carefully

- `authored`
  - use as a mode adjective, not as a grand product family

- `evidence`
  - only where the repo already emits a sidecar/bundle and only if scoped carefully

### Avoid As Primary Category Terms

- `Evidence Pack`
  - do not use this as the canonical noun for this repo
  - cross-repo terminology already uses it differently

- `attestation`
  - do not use this as the primary noun for `Authored Pack`

- `seal` / `sealed`
  - do not use unless a stronger concrete cryptographic operation actually exists

## Recommended Verbs

This is the part that needs tightening.

### Primary User-Facing Verbs

- `assemble`
  - preferred primary create verb
  - honest meaning: gather selected artifacts into a deterministic pack

- `verify`
  - keep
  - honest meaning: check the presented pack against its manifest and receipt contract

- `inspect`
  - keep
  - honest meaning: view pack structure, metadata, and verification summary

- `export`
  - use for making zipped/shareable output
  - honest meaning: emit a transport form of an already assembled pack

### Machine/Disposable-Bin Verb

- `consume-bin`
  - preferred public-facing replacement for `stamp-bin`
  - rationale: this path is subtractive and moves files; the verb should say so

If renaming the command now is too disruptive:
- keep `stamp-bin` as a compatibility alias
- label it publicly as `consume-bin`

### Verbs To Demote

- `stamp`
  - keep only as a compatibility alias or low-level implementation term
  - do not make it the primary README/UI verb

- `seal`
  - do not use in current runtime copy

- `prove`
  - do not use as the main user-facing verb for this repo

## Exact Language Recommendation

### Recommended Top-of-README Shape

First paragraph:

> Authored Pack is a small deterministic tool for assembling, verifying, and inspecting bounded artifact sets.

Second paragraph:

> It emits a pack directory or pack zip with a manifest, receipt, stable pack root, and payload root. It does not claim governed attestation, world-state truth, or signed provenance.

Third paragraph:

> If you want governed execution evidence, that belongs to `Attestation Engine`. Authored Pack is the smaller manual/deterministic lane.

### Recommended CLI/User Copy

Prefer:
- `assemble`
- `verify`
- `inspect`
- `export`
- `consume-bin`

Avoid leading with:
- `stamp`
- `stamp-bin`
- `sealed`
- `evidence pack`

## Migration Guidance

### Lowest-Churn Path

1. Keep repo name `Authored Pack`.
2. Change README/UI/help copy first.
3. Introduce new user-facing verbs as aliases before removing old ones.
4. Keep old commands for compatibility for at least one release cycle.
5. Do not rename schemas just to satisfy language cleanliness if that would destabilize v1.

### Practical Sequence

1. README:
   - reposition as deterministic assembly tool
   - add explicit non-goals
   - mention `Attestation Engine` only as a stronger adjacent system

2. CLI help:
   - present `assemble` as the primary action
   - leave `stamp` working as alias
   - present `consume-bin` as the honest subtractive machine path

3. TUI labels:
   - replace `Stamp` with `Assemble`
   - replace `stamp-bin` labels with `Consume Bin` or equivalent

4. Docs:
   - stop using `Evidence Pack` as the repo’s main artifact/category term
   - stop using `seal` unless future architecture becomes real

## Acceptance Gate

Accept the repo positioning only if all of the following are true:

- a stranger can understand the repo without knowing the broader stack
- the repo is still interesting on its own
- the README makes the proof boundary explicit
- `Authored Pack` no longer sounds like a disguised synonym for `Attestation Engine`
- the verb set is cleaner and more honest than `stamp`
- the repo still feels alive, not sterilized

## One-Sentence Strategic Instruction

Keep `Authored Pack` public and alive as an honest deterministic assembly tool, but do not let it masquerade as the broader attestation runtime or as the canonical proof artifact category.
