# EPS Entropy Instantiation Red-Team Dossier

Generated from the live workspace on 2026-03-22.

Repo: current EPS workspace checkout

## Purpose

This is a hostile review snapshot, not a product spec. Use it to spot semantic drift, lifecycle bugs, and trust-boundary mistakes before public release.

## Short Read

EPS is not an RNG. It is a deterministic pack stamper/verifier that can optionally derive reproducible 32-byte material from rooted pack state. The current public artifact shape exposes enough metadata to reproduce that derivation.

The main risks to keep in mind are:
- public receipts can collapse seed secrecy
- TUI and core flows can diverge if receipt mutation happens after zip/bundle creation
- verification proves self-consistency, not provenance
- `entropy_pack.zip` is a projection, not an external trust boundary

## What the code is trying to do

- deterministically walk and hash an input directory
- build a canonical manifest
- derive optional seed material from the manifest root and staged-source metadata
- emit receipts, evidence bundles, and a public zip projection
- support a TUI staging flow and a destructive bin-consumption flow

## The biggest current questions

1. Is the product being described as entropy generation when it is actually deterministic packaging plus KDF output?
2. Are receipt, evidence bundle, and zip written from one final pack state, or can they diverge?
3. Which identity is primary for agents: payload bytes, manifest root, or derived seed?
4. What should remain public, and what should remain sealed for later break-glass use?

## Immediate takeaway

Treat the current tree as a transition point:
- current public mode needs lifecycle coherence first
- sealed/break-glass mode should be a separate design, not a silent extension of public receipts
- docs, CLI text, and TUI labels should use one honest vocabulary

## Reference Files

- `docs/SEALED_PACK_ARCHITECTURE.md`
- `docs/CLEAR_DECK_PLAN.md`
- `README.md`
- `eps/pack.py`
- `bin/eps.py`
