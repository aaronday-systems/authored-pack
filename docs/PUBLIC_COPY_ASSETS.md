# Public Copy Assets

## Short Product Description

Authored Pack is a small deterministic pack/verify tool for humans and agents. It produces legible packs with stable roots, receipts, and verifiable payload records.

## Medium Product Description

Authored Pack packages a normal folder or deliberately staged authored sources into a deterministic pack with a canonical manifest, a receipt, and stable rooted identities. You can verify the pack later as a directory or zip and check that the presented bytes still match the pack contract. It is useful when you want a small honest tool for deliberate human input, auditability, and handoff integrity. It is not an RNG, not automatic secrecy, and not signed provenance.

## Website Copy Block

Authored Pack is a small deterministic pack/verify tool. Give it a normal folder or deliberately staged authored sources and it gives you a legible pack with a manifest, receipt, stable pack root, and payload root. Verify it later, hand it off, or route it into another tool. Public v1 is deterministic pack/verify only. Sealed mode is future design work, not current runtime behavior.

## X / Twitter Draft

I released a small deterministic pack/verify tool for humans and agents.

It makes a legible pack with:
- a canonical manifest
- a receipt
- a stable pack root
- a payload root you can verify later

It is not an RNG, not secrecy theater, and not a platform claim. Just a small honest tool.

Repo + demo: <add public link>

## LinkedIn Draft

I released Authored Pack, a small deterministic pack/verify tool for humans and agents.

The point is not “AI magic” or a grand security story. The point is a legible artifact: a pack with a canonical manifest, a receipt, stable rooted identity, and a clear verify step.

If you need a small honest tool for deliberate operator input, auditability, and handoff integrity, that is what this is for.

What it is not:
- not an RNG
- not automatic secrecy
- not signed provenance
- not a trust platform

Public v1 is deterministic pack/verify only. Sealed mode is still future design work.

## What It Is / What It Isn’t

What it is:
- a small deterministic tool
- for packaging and verifying normal folders or deliberately staged authored sources
- with legible packs, stable roots, and receipts

What it is not:
- not an RNG
- not automatic secrecy
- not signed provenance
- not sealed break-glass storage in v1

## Screenshot / Demo Shot List

1. Repo front door
- README top section with the first four lines visible
- shows the product boundary immediately

2. TUI first screen
- calm mode
- left menu on `Start`
- right pane showing the first-success path

3. TUI stamp review
- `Stamp` selected
- inline review panel open
- compact rows visible, no prompt ladder

4. CLI stamp success
- one short terminal capture showing `pack_dir`, `pack_root_sha256`, `payload_root_sha256`, `zip_path`

5. CLI verify success
- one short terminal capture showing `ok`, `pack_root_sha256`, verified counts

6. Pack directory glimpse
- show `manifest.json`, `receipt.json`, `pack_root_sha256.txt`, `entropy_pack.zip`

## Short Video / Capture Plan

Length target: 25 to 40 seconds.

Sequence:
1. Open on the TUI `Start` screen for 2 to 3 seconds.
2. Cut to `Stamp` review panel with clean defaults.
3. Cut to terminal running `bash scripts/demo_v1.sh`.
4. Let `stamp` finish and hold on the root output for 1 second.
5. Let `verify` dir and `verify` zip succeed.
6. End on the pack directory contents or README top block.

Narration / caption line:
- “A small deterministic tool for packaging and verifying normal folders or deliberately staged authored sources.”

Avoid:
- seed demos
- secrecy claims
- long path dumps
- noisy-mode ceremony in the first public clip
