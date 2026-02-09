# TUI Standard (v0.1.0, neutral)

Control Plane is the **reference implementation and source of truth** for this terminal UI standard. No separate "spec repo" exists; copy from here. The standard is neutral: branding and marks are per-app, not shared.

## 1) Scope
- Shared contract for terminal UIs and CLI headers.
- Control Plane hosts the contract **and** the reference implementation (`bin/control-plane.py`).
- Other apps may extend visuals but must keep the baseline rules intact.

## 2) Non-negotiables (baseline)
- ASCII-first. ANSI color is optional; stripping color must not remove meaning.
- No Unicode box-drawing, emoji, or other non-ASCII in baseline layouts.
- No “truth-claim” badges (e.g., “deterministic YES”). Only show configured or computed state.
- Output must be safe to paste into `.md`/`.txt`/logs/terminals:
  - No accidental Markdown headings (`# `) in divider lines.
  - No plain `---`, `***`, or `___` lines that would be interpreted as Markdown rules.

## 3) Identity tokens (renderer-agnostic)
Each app defines:
- `PRODUCT` — e.g., `CONTROL PLANE`, `GOBLINRADAR`
- `VERSION` — e.g., `v0.3.0`
- `MARK` — optional short ASCII prefix (may be empty)

Rules:
- The standard does **not** prescribe any specific `MARK`.
- Control Plane may choose no mark or a neutral one. GoblinRadar’s `(+))))` is **GoblinRadar-only** and must not be the default here.
- Renderers treat marks as plain text.

## 4) Shared semantic fields (canonical keys)
Header/status keys are reserved and must keep their semantics:
- `MODE` — list of tags, e.g., `offline`, `no-net`, `local-fs`
- `INPUT` — input type, e.g., `jsonl`, `ssot`
- `RISK` — one of `{OK, INFO, WARN, CRIT}`
- `ACTION` — optional follow-up, e.g., `reask_or_rerun`
- `f=<int>` — flags count
- `e=<int>` — evidence count
- `ref=<token>` — short hash/identifier

Other fields are allowed, but reusing these names with shifted semantics is not.

## 5) Width and layout rules

### 5.1 Buckets
- Wide: `cols >= 80`
- Narrow: `cols <= 60`
- Renderers must provide a wide variant and a narrow fallback. Anything between 61–79 may choose the narrow layout.

### 5.2 Header bands (three lines)
1. Identity line: `[MARK ]PRODUCT VERSION`
2. Status line: formatted key/value fields using the shared keys.
3. Divider line: ASCII-only, contains at least one non-hyphen character.

### 5.3 Dividers (Markdown-safe)
Examples that satisfy the rule:
- 80 cols: `-------+-------+-------+-------+-------+-------+-------+-------+-------+-------+`
- 60 cols: `-------+-------+-------+-------+-------+-------+-------+----`

### 5.4 Key–value formatting
- Named fields: `KEY: value`
- Count suffixes: `f=<n> e=<n>` (space-separated)
- Two spaces between named key/value pairs; one space between the count suffixes.

## 6) Header examples (mark-neutral)

Wide (`>=80`):
```
CONTROL PLANE v0.3.0
MODE: offline  INPUT: ssot  RISK: INFO  ACTION: none  f=0 e=2 ref=cp
-------+-------+-------+-------+-------+-------+-------+-------+-------+-------+
```

Narrow (`<=60`):
```
CONTROL PLANE v0.3.0
MODE: offline  RISK: INFO  f=0 e=2
-------+-------+-------+-------+-------+-------+-------+----
```

## 7) Branding separation
- Control Plane stays neutral. App marks/branding are opt-in per app.
- GoblinRadar’s `(+))))` belongs only to GoblinRadar and must never be the default in this repo.

## 8) Conformance cues
- Reference implementation: `bin/control-plane.py` (curses, ASCII-first).
- Palette and UI assets live under `ssot/ui/` and inherit this standard.
