# Sealed Pack Architecture

Date: 2026-03-22
Status: future design only; not implemented in Authored Pack v0.2.1
Scope: define the next object model for secret-preserving Authored Pack packs without changing current runtime behavior

## 1. Product Boundary

Authored Pack is not an RNG.

The correct framing is:
- the operator brings deliberate or secret input material
- Authored Pack contributes deterministic packaging, hashing, commitment, schema, verification, and optional derivation
- Authored Pack may then emit agent-ingestion instructions against that packaged material

This is the future-design one-line description for sealed mode only, not the current public pitch:

> Authored Pack turns deliberate secret material into a legible, verifiable pack, and can optionally seal that pack for break-glass use.

That means Authored Pack should separate two modes clearly:
- `public deterministic pack`: verifiable and reproducible, not secret by default
- `sealed break-glass pack`: confidentiality-preserving, tamper-evident, not publicly reproducible in the same way

## 2. Problem Statement

Current public Authored Pack packs are suitable for:
- deterministic packaging
- internal consistency checks
- operator auditability
- reproducible KDF output when publication is acceptable

Current public Authored Pack packs are not suitable for:
- secrecy after publication
- claims of fresh randomness generation
- first-open proof
- provenance claims stronger than self-consistency

If Authored Pack is going to support "break glass to use" packs, the design must add a separate sealed envelope instead of stretching the current public receipt model.

## 3. Design Goals

1. Preserve the current public deterministic mode as an honest, verifiable, non-secret mode.
2. Add a sealed mode where secret inputs and optional derived material remain confidential at rest.
3. Keep the outer contract legible enough for operators and downstream agents.
4. Make tampering detectable.
5. Avoid semantic overclaiming: no offline first-open proof, no fake entropy claims.
6. Keep the implementation testable and versioned.

## 4. Non-Goals

This design does not promise:
- true RNG behavior
- unbiased public randomness
- offline proof that a pack was never opened before
- provenance stronger than whatever external signatures or witnesses are actually configured
- strong secrecy if the decrypted contents or private keys are later disclosed

## 5. Trust Model

### 5.1 Public Deterministic Pack

This is the current Authored Pack family:
- root and receipt can be public
- verification means self-consistency of the presented artifact
- any deterministic derived seed material is reproducible from disclosed derivation inputs

Use this mode when:
- reproducibility is desired
- secrecy is not the point
- operators want auditable commitments and optional downstream instructions

### 5.2 Sealed Break-Glass Pack

This is the new mode proposed here:
- outer metadata is public or semi-public
- secret payload and optional derived seed remain encrypted
- break-glass access requires possession of the right decryption capability
- opening can produce an access receipt, but offline opening cannot by itself prove prior unopened state

Use this mode when:
- the packed material must remain confidential until use
- the operator wants to archive high-value material and only decrypt it in a deliberate break-glass event
- downstream agents may consume the contents only after explicit decryption

## 6. Core Principle: Split Public Commitment From Secret Contents

Current public Authored Pack roots identify a manifest that may disclose enough to reproduce derived seed material.
That is acceptable only for public deterministic packs.

Sealed mode must split the world into:
- an **outer public seal**
- an **inner encrypted envelope**

The outer public seal identifies the sealed artifact.
The inner encrypted envelope contains the actual secret-bearing material.

This is the main architectural move.

## 7. Proposed Object Model

### 7.1 Outer Public Seal

New public files for sealed mode:
- `seal.json`
- `sealed_payload.bin`
- optional `seal.sig`
- optional `instructions.public.json`
- optional `seal_receipt.json`

The outer object should reveal only what is safe to reveal before opening.

Suggested `seal.json` fields:
- `schema_version`: `authored.seal.v1`
- `tool`: `authored-pack`
- `tool_version`
- `mode`: `sealed-break-glass`
- `cipher_suite`
- `recipient_policy`
- `ciphertext_sha256`
- `ciphertext_size_bytes`
- `public_instructions_sha256` when present
- `seal_commitment_sha256` if we want a self-identifier for the outer seal
- optional `created_at_utc`
- optional `operator_note_public`

Important rule:
- `seal.json` must not disclose the inner derivation root if the pack is intended to keep derived seed material secret.

### 7.2 Inner Encrypted Envelope

The encrypted payload should contain the real secret-bearing contents.
Suggested inner files:
- `inner_manifest.json`
- `payload/**` or a packed payload container
- optional `derived_seed.bin` or `derived_seed.hex`
- optional `instructions.private.json`
- optional `source_audit/**`

Suggested `inner_manifest.json` fields:
- `schema_version`: `authored.inner_manifest.v1`
- `content_root_sha256`
- `artifact manifest`
- optional `derivation`
- optional `source_audit_root`
- optional `agent_schema_ref`

The inner envelope can use the same discipline as current Authored Pack:
- canonical JSON
- deterministic hashing
- explicit artifact records

But its root remains private because the whole envelope is encrypted.

## 8. Identity Model

Authored Pack should stop forcing one root to mean everything.

Sealed mode should use at least three identities:

1. `content_root_sha256`
- commitment to the inner content manifest and secret-bearing structure
- private unless explicitly disclosed after opening

2. `seal_commitment_sha256`
- commitment to `seal.json`
- public
- stable identifier for the sealed artifact itself

3. `ciphertext_sha256`
- direct integrity fingerprint of `sealed_payload.bin`
- public
- useful for storage, mirroring, and signature binding

This separation matters because:
- a public seal identity should not automatically reveal the derivation root
- a content root should not be used as the public storage identity if that collapses secrecy

## 9. Derivation Model For Sealed Packs

There are two honest derivation families.

### 9.1 Public Deterministic Derivation

This is the current model:
- deterministic from disclosed rooted inputs
- reproducible by anyone with the published pack
- not secret after disclosure

Keep it, but describe it honestly.

### 9.2 Secret Sealed Derivation

If the goal is to keep derived material secret, one of these must be true:
- the derivation inputs are not publicly disclosed
- or a separate secret input is mixed into derivation

For sealed packs, the simpler approach is:
- derive inside the inner encrypted envelope
- do not expose the inner derivation root in the outer seal

That preserves the principle the user stated:
- operator brings deliberate secret inputs
- Authored Pack contributes rigor and hashing
- the result can remain secret until break-glass decryption

A stronger variant is to allow an additional operator-held secret or hardware-held secret to participate in derivation, but that should be a separate versioned mode, not quietly mixed into the first sealed design.

## 10. Encryption and Signing Layers

### 10.1 Confidentiality Layer

Sealed mode needs authenticated encryption for the inner envelope.
The architecture should stay abstract until implementation choice is locked, but the envelope must provide:
- confidentiality
- ciphertext integrity
- recipient-key-based access control or threshold access policy

Examples of acceptable implementation families:
- age/X25519-based recipient encryption
- libsodium sealed boxes or equivalent recipient encryption
- cloud/HSM-managed envelope encryption

The implementation choice is secondary to the object model.

### 10.2 Signature Layer

`seal.sig` should sign the outer public seal.
At minimum, the signature should cover:
- canonical `seal.json`
- `ciphertext_sha256`

This provides:
- tamper evidence relative to the signing key
- operator or system identity, if keys are managed properly

Without signatures, the seal is only locally hashed, not strongly attested.

## 11. "Opened Before" Semantics

This must be stated precisely.

Offline cryptography cannot prove strong first-open status by itself.
A copied encrypted file can be opened many times without the file itself becoming capable of proving prior access.

So the design should separate:

1. **sealed at rest**
- yes, achievable

2. **tamper-evident seal**
- yes, achievable with signatures and integrity checks

3. **break-glass access receipt**
- yes, achievable locally at open time

4. **provable first-open event**
- not achievable offline
- requires an external witness or hardware trust anchor

The honest implementation target is:
- sealed packs can be opened deliberately
- the open action can be recorded
- but the pack itself cannot prove it was never opened before without external support

## 12. Immediate Implementation Boundary

Do not code sealed mode until the public mode is clean.
The next coding pass should only finalize the public artifact lifecycle and naming so sealed mode starts from a coherent baseline.
