# Public Voice Brief

Maintainer note: this is a maintainer-facing voice contract for README text, release notes, and other public-facing copy. It is not part of the runtime or schema contract.

Owner: Dev Architect

## Purpose

Keep Authored Pack public writing clear, factual, and durable.

The goal is not “better prose” in the abstract. The goal is trust calibration:
- say what the tool is
- say what it is not
- explain history only when it clarifies the current shape
- avoid overclaiming

## Canonical Structure

When public copy needs history or framing, use this order:

1. origin question
2. first practical response
3. explicit correction
4. durable core
5. hard boundary

Example shape:
- what question produced the work
- what the first implementation instinct was
- what was wrong with the earlier framing
- what survived as the honest core
- what the tool is not

## Voice Rules

- factual, not promotional
- causal, not mystical
- concrete nouns over abstractions
- short declarative sentences over ornamental transitions
- willing to say the old framing was wrong when that is true
- history only when it explains the present implementation
- end with a hard boundary statement when boundaries matter

## Good Patterns

- “The name was wrong.”
- “What survived that correction was the durable core.”
- “That is Authored Pack now: ...”
- “Not an entropy source. Not a proof system. Not an attestation engine.”

## Bad Patterns

- vague mythology about origin or intention
- product-marketing language like `platform`, `reimagines`, `unlocks`, `journey`, or `vision`
- implying randomness, secrecy, proof, or attestation properties that the tool does not actually provide
- softening a correction that should be stated plainly
- generic archive-tool language that erases manual staging, receipts, and bounded artifact sets

## Release Notes Rules

- Start from `docs/RELEASE_NOTES_TEMPLATE.md`
- Keep release notes factual and current-commit specific
- Do not restate the whole README
- Do not introduce new product claims in release notes
- Mention boundary-sensitive changes explicitly: license, trust boundary, public verbs, release contract, compatibility aliases

## Enforcement

- `AGENTS.md` points public-surface and release-note work here
- `tests/test_public_release_contract.py` checks that this brief and the release-notes template exist and contain the required structure
- `scripts/release_check.sh` runs that contract test as part of the canonical gate
