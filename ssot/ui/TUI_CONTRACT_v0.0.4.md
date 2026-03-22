# TUI Contract (v0.0.4, historical/reference-only)

This contract defined a deterministic, human-centered operator console pattern for terminal UIs.
For EPS, it is now historical/reference-only. The normative EPS UI baseline is `ssot/ui/TUI_STANDARD_v0.1.0.md`.
If a TUI conflicts with this contract, that may still be acceptable for legacy or experimental modes, but it is no longer the EPS conformance target unless the contract is version-bumped.

Design posture: calm, legible, procedure-driven. One strong move; everything else quiet.

## 0. Authority and change control

- Normative for all new operator-style TUIs in this ecosystem.
- Existing TUIs may be partial until migrated.
- Preferences are not change requests. Only versioned contract changes are allowed.
- Any change to semantics or invariants requires:
  - a version bump, and
  - a short rationale note in the changelog for the app.

## 1. Terminal baseline (hard constraints)

- ANSI 16-color compatible.
- Minimum terminal size: 80 columns x 24 rows.
- Must remain understandable in monochrome. Color is never the only carrier of meaning.
- ASCII must remain sufficient. Unicode may be cosmetic only.
- If terminal is below minimum size, show a clear warning and avoid unsafe actions.

## 2. Layout invariant (non-negotiable regions)

Top-to-bottom, regions never move; only contents change.

- Header row: exactly 1 line
  - left: app name
  - right: version/branch/build
- Tabs row: exactly 1 line
  - mode switching only (no actions/state mutation)
- Body: fixed split with exactly one vertical divider
  - left pane: navigation/actions list (single selection)
  - right pane: authoritative state + details for selection
- Footer row: exactly 1 line
  - key legend (always)
  - optional status counters (right-aligned when present)

## 3. Navigation invariant (muscle memory)

- Up/Down: move selection.
- Enter: primary action for the selected item.
- Esc or q:
  - in overlays (pickers/viewers/modals): go back
  - at root: quit
- No overloaded meanings for Up/Down/Enter/Esc/q.
- Any additional shortcuts must be shown in the footer legend.

## 4. Divider and grid (alignment is the UI)

### Divider

- Exactly one divider between panes.
- Preferred divider glyph: `│` (U+2502).
- ASCII fallback: `|` (U+007C). ASCII must remain sufficient.
- Divider occupies exactly 1 column. Right pane content starts immediately after the divider.

### Grid discipline

- Everything aligns to a consistent internal grid:
  - fixed columns for: selection marker, status glyphs, labels, values, counts.
- Tables must use fixed-width columns with explicit spacing; never rely on tabs.
- Avoid wrapping inside tables. Truncate deterministically (use `...`).

## 5. Selection and focus (standardized treatment)

- Exactly one selection treatment across the entire app:
  - full-row background fill on selected row
  - monochrome fallback: inverse-video full-row
- Selected row must include a non-color focus marker at a fixed column:
  - `>` at column 1, then space, then label
- Non-selected rows do not use bold.

## 6. Typography (weight budget)

- Monospace only.
- Bold is scarce. Bold permitted only for:
  - right pane title/section headers
  - critical labels inside the critical line (WARN/ERROR)
- Bold forbidden for:
  - header band text
  - body text (lists, tables, viewers)
  - routine values (paths, counters, IDs)

## 7. Right pane model (human-first, deterministic)

Right pane is authoritative. It always answers:
1) Is anything wrong?
2) What will happen if I press Enter?
3) What is the current state of the selected thing?

### 7.1 Two-layer rendering

The right pane is composed of two layers:

1) **Metadata layer** (fixed, compact, machine-parsable)
2) **Payload layer** (human-readable content)

Do not spam generic tags (e.g., `item:`, `line:`, `row:`) when the content is already self-describing.

### 7.2 Metadata layer (root screen)

When the user is at the root screen (not in a picker/viewer/modal), the top of the right pane reserves exactly:

- `critical: <glyph> <LABEL> <message>`
- `action: <risk> | <effect> [| targets:<targets>]`

Noise budget:
- For read-only and local non-destructive actions, metadata is limited to these 2 lines.
- For destructive/external actions, additional preview/confirm lines are permitted.

### 7.3 Payload layer (root screen)

After the metadata layer, payload content is rendered **raw** (no synthetic key prefixes):

- text previews (README, contracts, logs) render as plain lines
- tables render as fixed-width rows (header row + rows)
- lists render as plain lines (optional leading space is acceptable; avoid decorative bullets)

If ambiguity exists (example: a list mixing multiple item types), use a specific, meaningful key
(`repo:`, `launcher:`, `target:`) rather than generic `item:`.

### 7.4 Overlays (pickers/viewers)

Overlays prioritize legibility:

- Viewer payload is raw file content (no `line:` prefixes).
- Picker items are raw labels with the selection marker; avoid `item:` tagging.

The `critical:` line may remain visible, but must not prevent the payload from being readable at 80x24.

## 8. Critical states (redundancy, never color-only)

Warnings/errors/critical failures must never be color-only.

Critical states must include all of:
- glyph (example: `.`, `!`, `X`)
- label (`OK`, `WARN`, `ERROR`)
- fixed location (the `critical:` line in the right pane)

Recommended mapping:
- OK: `. OK <message|none>`
- WARN: `! WARN <message>`
- ERROR: `X ERROR <message>`

## 9. Color contract (semantic vs accent)

Semantic hues are global and invariant:
- Red = error / failure / invalid / stop
- Yellow = warning / caution / check / approaching limit
- Green = nominal / ready / in-tolerance
- Gray = inactive / disabled / unavailable
- White/default = neutral / labels / advisory

Rules:
- Semantic colors use bright intensity when available.
- Accents use normal (non-bright) intensity.
- Accent surfaces allowed only on:
  - header background band
  - tab highlight
  - selection background
- Accents must not use bright red/yellow/green.
- One accent hue per TUI.

## 10. State model (safe, visible, serialized)

Right pane reports the latest completed state, not a promise.

Canonical action state machine:
- idle -> needs_confirm -> running -> success|error -> idle
- blocked is allowed (dependency missing/unsafe/disconnected)

While running:
- show `running` state in the footer and/or right pane
- prevent competing actions on the same resource unless explicitly designed

## 11. Action risk classes and safety gates

Every action must declare exactly one risk class:
1) read-only
2) local non-destructive
3) local destructive
4) external/exfiltration
5) irreversible destructive

The metadata layer must show the risk and effect in the `action:` line.
Targets must be shown when they materially affect safety (destructive/external actions).

Confirm requirements:
- multi-item destructive (N > 1): 2-step confirmation
- external/exfiltration: preview + explicit confirm
- irreversible destructive: preview + typed token confirm

External/exfil preview must show:
- destination
- file count
- total size (if applicable)
- redaction/sensitivity status (if applicable)

## 12. Footer legend (non-optional)

Footer always shows:
- Up/Down movement keys
- Enter for primary action
- Esc/q back or quit semantics (overlay vs root)

If extra shortcuts exist, they must appear in the legend.

## 13. Machine-readable export (canonical if present)

`ssot/ui/PALETTE.json` is the canonical export for tools/tests and must match this contract.

Recommended fields:
- terminal.min_cols/min_rows/palette/monochrome_ok
- semantic hues + intensity (bright vs normal)
- accents.allowed_surfaces + forbidden bright hues + single accent hue
- selection.treatment + marker + monochrome_fallback
- redundancy.critical_states
- divider.preferred + divider.ascii_fallback
- right_pane.metadata_format + right_pane.payload_format

## 14. Conformance tracking

Per-app conformance status lives in `ssot/ui/UI_CONFORMANCE.md`.
