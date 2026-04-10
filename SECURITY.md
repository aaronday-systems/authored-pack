# Security Policy

## Scope

Authored Pack is a deterministic packaging and verification tool. It is not an RNG, not automatic secrecy, and not signed provenance.

Security-sensitive areas include:
- manifest and receipt integrity
- pack/zip finalization coherence
- path traversal and symlink handling
- overlap guards for input/output/bin paths
- accidental disclosure of derived seed material

## Supported Release Line

The current supported public line is:
- `v0.2.x`

Older pre-`v0.2.1` states are historical development milestones and should not be treated as supported public contracts.

## Reporting A Vulnerability

Preferred path:
- use GitHub private vulnerability reporting for this repository if it is enabled

If private reporting is not enabled yet:
- do not open a public issue for an unfixed vulnerability
- contact the maintainer directly through GitHub first

Please include:
- affected version or commit
- exact reproduction steps
- expected behavior
- actual behavior
- impact assessment
- any proposed fix direction if you have one

## What Authored Pack Does And Does Not Promise

Authored Pack currently provides:
- deterministic packaging
- internal consistency verification
- optional reproducible derived seed material
- local tamper-evident evidence bundles

Authored Pack does not currently provide:
- fresh randomness generation
- secrecy once a public receipt discloses derivation inputs
- signed provenance
- offline proof that a pack was never opened before

## Disclosure Expectations

Give the maintainer reasonable time to reproduce and patch before public disclosure.
