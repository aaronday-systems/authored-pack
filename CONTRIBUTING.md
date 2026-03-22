# Contributing

EPS is a deterministic pack/verify tool for operator-supplied entropy-bearing inputs.

## Supported Python

- Python 3.11
- Python 3.12

## Canonical Test Command

```bash
pytest -q
```

Also run the module smoke check before opening a release-sensitive pull request:

```bash
python3 -m eps --help
```

## Change Shape

Keep changes small, explicit, and reversible.

Preferred shape:
- one focused concern per pull request
- tests in the same change when behavior or contracts move
- docs updated when CLI, manifest, receipt, zip, or TUI operator wording changes

## Contract-Sensitive Areas

If you change any of the following, you must add or update tests in the same pull request:
- `manifest.json` schema or canonicalization
- `receipt.json` schema or finalization order
- `entropy_pack.zip` contents or verification rules
- CLI `--json` envelopes
- TUI flows that affect receipt, audit, or derived-seed handling

## Branching / PR Expectations

- Use short-lived topic branches.
- Keep diffs reviewable.
- Describe trust-boundary changes plainly in the PR body.
- Call out any compatibility impact on existing packs or downstream tooling.

## Before You Open A PR

Run:

```bash
pytest -q
python3 -m pytest -q
python3 -m eps --help
```

If the change touches release-facing docs, also verify the README commands still match the runtime behavior.
