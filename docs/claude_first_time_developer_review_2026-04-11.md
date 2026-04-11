# Claude First-Time Developer Review Handoff — 2026-04-11

Maintainer note: this is a repo-local review prompt, not part of the current public product contract.

## Why This Exists

Authored Pack started from an older EPS / entropy-pack-stamper lane and is now positioned as a deterministic pack/verify tool for bounded artifact sets.

This handoff is for asking Claude to read the repo like a first-time developer, not like someone who already knows the backstory.

## Files To Read

- `/Users/aaronday/dev/authored-pack/README.md`
- `/Users/aaronday/dev/authored-pack/CONTRIBUTING.md`
- `/Users/aaronday/dev/authored-pack/docs/CANONICAL_DEMO.md`

Optional runtime check:

- `python3 -m authored_pack --help`

## Reviewer Lens

Please review this repo from the point of view of a first-time developer trying to answer:

- what this tool is
- what it is not
- how to run it from a clone
- whether the examples are concrete and honest
- whether older entropy-pack-stamper framing still leaks through

## Current Context

- The repo should read as a deterministic assemble/verify tool for bounded artifact sets.
- It should not read as governed attestation, signed provenance, secrecy tech, or a universal evidence runtime.
- The README now includes three example lanes:
  - manual source bundle
  - bug repro bundle
  - session handoff bundle
- We want the repo to feel interesting and usable without overstating what it proves.
- We are intentionally trying to remove leftover entropy-pack-stamper lineage language from the front door.
- We are intentionally treating derived-seed support as an advanced option, not the first thing a new reader should optimize around.

## Changes Applied After The First Review

- Removed the explicit entropy-pack-stamper lineage note from the README examples.
- Rewrote the first example as `Manual source bundle`.
- Added a real clone-first Quick Start block to the README.
- Moved derived-seed wording into one advanced note in the README instead of repeating it as a front-door claim.
- Made the README explain when `pack_root_sha256` vs `payload_root_sha256` matters in practical terms.
- Updated CLI help so `stamp` and `stamp-bin` still work but no longer lead the help table.
- Updated CLI help so derived-seed support reads as an advanced option, not the primary product pitch.

## Questions For Claude

1. After one README pass, what do you think this tool is for?
2. Which sentence or section still feels most confusing?
3. Do the example use cases help, or do they still feel abstract?
4. Does the run-from-clone path feel clear enough for a first-time developer?
5. What is the single highest-leverage README or docs change you would make next?
6. Does the README now keep derived-seed language in the right place, or does it still feel too early?
7. Does the CLI help now look like the surface of a tool a first-time developer could trust?

## Ready Prompt

```text
Repo: /Users/aaronday/dev/authored-pack

Read:
- README.md
- CONTRIBUTING.md
- docs/CANONICAL_DEMO.md

Then run:
- python3 -m authored_pack --help

Review this repo from the point of view of a first-time developer trying to understand what the application is, what it is not, and how they would use it from a clone.

Return:
1. your one-sentence understanding of the product
2. the top 3 confusing or misleading points
3. whether the example use cases are concrete and honest
4. whether the CLI/run path is clear
5. the single highest-leverage improvement to README structure or wording
6. whether derived-seed support now feels appropriately demoted for a first-time reader
7. whether the CLI help now feels clean and first-time-developer-ready

Important constraints:
- Keep Authored Pack framed as a deterministic pack/verify tool for bounded artifact sets.
- Do not reframe it as attestation, signed provenance, secrecy tech, or a universal evidence runtime.
- Call out any leftover entropy-pack-stamper mental-model leakage if you see it.
```
