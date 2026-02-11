#!/usr/bin/env python3
"""
Minimal curses TUI for Entropy Pack Stamper (EPS).

Baseline: copy the Control Plane interaction posture:
- 3-line header band (identity + status + divider)
- left menu + right preview/log
- action execution runs outside curses (prompt -> run -> return)

ASCII-first: no box-drawing or emoji. Color is optional.

To intentionally break the baseline TUI rules (loud palette, unicode dividers),
run: `python3 -B bin/eps.py --insane`
"""

from __future__ import annotations

import argparse
import base64
import curses
import hashlib
import json
import math
import os
import random
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

# When running as `python3 bin/eps.py`, Python prepends `bin/` to sys.path,
# which would cause `import eps` to resolve to this file (bin/eps.py).
# Force repo-root precedence so `eps/` package imports work.
_BIN_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BIN_DIR.parent
try:
    sys.path.remove(str(_BIN_DIR))
except ValueError:
    pass
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eps import __version__
from eps.pack import DEFAULT_MAX_MANIFEST_BYTES, stamp_pack, verify_pack, write_evidence_bundle


APP_NAME = "ENTROPY PACK STAMPER"
APP_VERSION = f"v{__version__}"
BUNDLED_GODEL_WORDS = _REPO_ROOT / "assets" / "godel_words.txt"

DIVIDER_WIDE = "-------+-------+-------+-------+-------+-------+-------+-------+-------+-------+"
DIVIDER_NARROW = "-------+-------+-------+-------+-------+-------+-------+----"


def safe_addstr(stdscr, y: int, x: int, s: str, attr: int = 0) -> None:
    try:
        stdscr.addstr(y, x, s, attr)
    except UnicodeEncodeError:
        # Some terminals / locales can choke on non-ASCII. Fall back to a safe
        # representation instead of crashing the whole UI.
        try:
            safe = s.encode('ascii', errors='replace').decode('ascii')
        except Exception:
            safe = '?'
        try:
            stdscr.addstr(y, x, safe, attr)
        except curses.error:
            return
    except curses.error:
        return


def _is_hidden_rel(rel_posix: str) -> bool:
    parts = str(rel_posix).split("/")
    return any(p.startswith(".") and p not in (".", "..") for p in parts)


def _scan_artifacts_for_picker(input_dir: Path, *, include_hidden: bool) -> List[Tuple[str, int]]:
    """
    Deterministic file scan (path + size) without hashing, for interactive exclude selection.
    Matches eps.manifest._iter_files traversal order.
    """
    input_dir = Path(input_dir).resolve()
    out: List[Tuple[str, int]] = []
    for dirpath, dirnames, filenames in os.walk(input_dir):
        dirnames.sort()
        filenames.sort()
        base = Path(dirpath)
        for name in filenames:
            p = base / name
            try:
                rel = p.relative_to(input_dir).as_posix()
            except Exception:
                continue
            if not include_hidden and _is_hidden_rel(rel):
                continue
            try:
                if p.is_symlink() or (not p.is_file()):
                    continue
                size = int(p.stat().st_size)
            except OSError:
                continue
            out.append((rel, size))
    return out


def _artifact_exclude_picker(stdscr, state: "AppState", *, input_dir: Path, include_hidden: bool) -> Optional[Set[str]]:
    """
    Overlay picker: select artifacts to exclude from the next stamp (non-destructive).

    Keys:
    - Up/Down: move
    - Space: toggle exclude
    - /: filter (substring)
    - A: include all (clear excludes)
    - X: exclude all (within filter if set, else all)
    - Enter: done/apply
    - Esc/q: cancel
    """
    rows, cols = stdscr.getmaxyx()
    if rows < 24 or cols < 80:
        # Contract minimum; don't attempt a complex overlay.
        msg = "Terminal too small for artifact picker (need >= 80x24)."
        stdscr.erase()
        safe_addstr(stdscr, rows // 2, max(0, (cols - len(msg)) // 2), msg[:cols], curses.A_REVERSE)
        safe_addstr(stdscr, min(rows - 2, rows // 2 + 2), max(0, (cols - 34) // 2), "Resize and try again. Press any key.", curses.A_REVERSE)
        stdscr.refresh()
        try:
            stdscr.getch()
        except curses.error:
            pass
        return None

    items = _scan_artifacts_for_picker(Path(input_dir), include_hidden=bool(include_hidden))
    total = len(items)
    if total == 0:
        return set()

    excludes: Set[str] = set()
    query = ""
    sel = 0
    top = 0

    def _matches(rel: str) -> bool:
        if not query:
            return True
        return query.lower() in rel.lower()

    def _filtered_indices() -> List[int]:
        return [i for i, (rel, _sz) in enumerate(items) if _matches(rel)]

    filtered = _filtered_indices()

    header_h = 4
    footer_h = 2
    list_top = header_h
    list_h = max(1, rows - header_h - footer_h)

    try:
        stdscr.nodelay(False)
        stdscr.timeout(-1)
    except curses.error:
        pass

    while True:
        stdscr.erase()
        title = "ARTIFACTS // EXCLUDE FROM NEXT STAMP"
        sub = f"input: {str(Path(input_dir).resolve())}"
        stats = f"total={total}  match={len(filtered)}  excluded={len(excludes)}  hidden={'on' if include_hidden else 'off'}"
        filt = f"filter: {query or '(none)'}   (press / to edit)"

        attr_t = state.palette.header[0] if (state.insane and state.palette) else state.theme.header
        attr = state.palette.text if (state.insane and state.palette) else state.theme.normal
        safe_addstr(stdscr, 0, 0, title[:cols].ljust(cols), attr_t)
        safe_addstr(stdscr, 1, 0, sub[:cols].ljust(cols), attr)
        safe_addstr(stdscr, 2, 0, stats[:cols].ljust(cols), attr)
        safe_addstr(stdscr, 3, 0, filt[:cols].ljust(cols), attr)

        if filtered:
            sel = max(0, min(sel, len(filtered) - 1))
            if sel < top:
                top = sel
            if sel >= top + list_h:
                top = max(0, sel - list_h + 1)

            for row in range(list_h):
                idx_in_filtered = top + row
                y = list_top + row
                if idx_in_filtered >= len(filtered) or y >= rows - footer_h:
                    break
                i = filtered[idx_in_filtered]
                rel, sz = items[i]
                selected = (idx_in_filtered == sel)
                mark = ">" if selected else " "
                chk = "X" if rel in excludes else " "
                size_s = _fmt_bytes(int(sz)).rjust(9)
                line = f"{mark} [{chk}] {size_s}  {rel}"
                if selected:
                    line_attr = (state.palette.menu_hot[0] if (state.insane and state.palette) else state.theme.reverse)
                else:
                    line_attr = attr
                safe_addstr(stdscr, y, 0, line[:cols].ljust(cols), line_attr)
        else:
            safe_addstr(stdscr, list_top, 0, "No matches. Press / to change filter."[:cols].ljust(cols), attr)

        legend = "Up/Down: move  Space: toggle  /: filter  A: include all  X: exclude all  Enter: done  Esc/q: cancel"
        safe_addstr(stdscr, rows - 2, 0, legend[:cols].ljust(cols), attr)
        safe_addstr(stdscr, rows - 1, 0, _divider_for_width(cols)[:cols].ljust(cols), attr)
        stdscr.refresh()

        try:
            ch = stdscr.getch()
        except curses.error:
            ch = -1
        if ch == -1:
            continue
        if ch in (27, ord("q"), ord("Q")):
            return None
        if ch in (10, 13, curses.KEY_ENTER):
            return set(excludes)
        if ch in (curses.KEY_UP, ord("k")):
            sel = max(0, sel - 1)
            continue
        if ch in (curses.KEY_DOWN, ord("j")):
            sel = min(max(0, len(filtered) - 1), sel + 1)
            continue
        if ch == curses.KEY_PPAGE:
            sel = max(0, sel - 10)
            continue
        if ch == curses.KEY_NPAGE:
            sel = min(max(0, len(filtered) - 1), sel + 10)
            continue
        if ch == ord(" "):
            if filtered:
                rel, _sz = items[filtered[sel]]
                if rel in excludes:
                    excludes.remove(rel)
                else:
                    excludes.add(rel)
            continue
        if ch == ord("/"):
            q2 = _prompt_str_curses(stdscr, "(EPS) filter substring", default=query, max_len=200)
            query = q2.strip()
            filtered = _filtered_indices()
            sel = 0
            top = 0
            continue
        if ch in (ord("a"), ord("A")):
            excludes.clear()
            continue
        if ch in (ord("x"), ord("X")):
            if filtered and query:
                for i in filtered:
                    excludes.add(items[i][0])
            else:
                excludes = {rel for (rel, _sz) in items}
            continue


@dataclass
class Theme:
    normal: int
    reverse: int
    header: int


@dataclass
class InsanePalette:
    # These are *attributes* (e.g. curses.color_pair(n)), not raw color IDs.
    bg: List[int]
    header: List[int]
    menu_hot: List[int]
    menu_dim: int
    divider: int
    text: int
    ok: int
    warn: int
    info: int


def _init_pair_safe(pair_id: int, fg: int, bg: int) -> None:
    try:
        curses.init_pair(pair_id, fg, bg)
    except curses.error:
        pass


def init_theme() -> Theme:
    normal = curses.A_NORMAL
    reverse = curses.A_REVERSE
    header = curses.A_REVERSE
    if curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()
            amber = 214 if getattr(curses, "COLORS", 0) >= 256 else curses.COLOR_YELLOW
            bg = curses.COLOR_BLACK
            curses.init_pair(1, amber, bg)
            curses.init_pair(2, bg, amber)
            normal = curses.color_pair(1)
            reverse = curses.color_pair(2)
            header = reverse
        except curses.error:
            normal = curses.A_NORMAL
            reverse = curses.A_REVERSE
            header = curses.A_REVERSE
    return Theme(normal=normal, reverse=reverse, header=header)


def init_insane_palette() -> InsanePalette:
    # Electric palette tuned for 256-color terminals, with a fallback for 16-color.
    if not curses.has_colors():
        return InsanePalette(
            bg=[curses.A_NORMAL],
            header=[curses.A_REVERSE],
            menu_hot=[curses.A_REVERSE],
            menu_dim=curses.A_NORMAL,
            divider=curses.A_NORMAL,
            text=curses.A_NORMAL,
            ok=curses.A_NORMAL,
            warn=curses.A_NORMAL,
            info=curses.A_NORMAL,
        )

    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass

    is_256 = getattr(curses, "COLORS", 0) >= 256
    max_pairs = int(getattr(curses, "COLOR_PAIRS", 0) or 0)
    pink = 201 if is_256 else curses.COLOR_MAGENTA
    cyan = 51 if is_256 else curses.COLOR_CYAN
    green = 46 if is_256 else curses.COLOR_GREEN
    yellow = 226 if is_256 else curses.COLOR_YELLOW
    purple = 93 if is_256 else curses.COLOR_BLUE
    white = 231 if is_256 else curses.COLOR_WHITE
    black = 0 if is_256 else curses.COLOR_BLACK

    bg0 = 17 if is_256 else black
    bg1 = 18 if is_256 else black
    bg2 = 52 if is_256 else black
    bg3 = 53 if is_256 else black
    # Extra glitch backgrounds (256-color only).
    bg4 = 54 if is_256 else black
    bg5 = 55 if is_256 else black
    bg6 = 56 if is_256 else black
    bg7 = 57 if is_256 else black
    bg8 = 88 if is_256 else black
    bg9 = 89 if is_256 else black
    bg10 = 90 if is_256 else black
    bg11 = 91 if is_256 else black

    # Reserve a block of pair IDs for the insane skin.
    _init_pair_safe(11, pink, bg0)
    _init_pair_safe(12, cyan, bg1)
    _init_pair_safe(13, green, bg2)
    _init_pair_safe(14, yellow, bg3)
    _init_pair_safe(15, purple, bg0)
    _init_pair_safe(16, black, pink)
    _init_pair_safe(17, black, cyan)
    _init_pair_safe(18, black, green)
    _init_pair_safe(19, black, yellow)
    _init_pair_safe(20, black, purple)
    _init_pair_safe(21, white, bg0)
    _init_pair_safe(22, cyan, purple)
    _init_pair_safe(23, white, bg1)
    _init_pair_safe(24, white, bg2)
    _init_pair_safe(25, white, bg3)
    _init_pair_safe(26, white, bg4)
    _init_pair_safe(27, white, bg5)
    _init_pair_safe(28, white, bg6)
    _init_pair_safe(29, white, bg7)
    _init_pair_safe(30, white, bg8)
    _init_pair_safe(31, white, bg9)
    _init_pair_safe(32, white, bg10)
    _init_pair_safe(33, white, bg11)

    # Optional: generate a larger "video noise" background bank if we have pair slots.
    # Uses white-on-neon backgrounds so space-fills become pure color fields.
    extra_bg_pairs: List[int] = []
    if is_256 and max_pairs >= 120:
        neon_bgs = [
            16, 17, 18, 19, 20, 21, 22, 23, 24,
            52, 53, 54, 55, 56, 57,
            88, 89, 90, 91, 92, 93,
            124, 125, 126, 127, 128, 129,
            160, 161, 162, 163, 164, 165,
            196, 197, 198, 199, 200, 201,
            202, 203, 204, 205,
            220, 221, 222, 223, 224, 225, 226, 227,
        ]
        pair_id = 40
        for bgc in neon_bgs:
            if pair_id >= max_pairs:
                break
            _init_pair_safe(pair_id, white, int(bgc))
            extra_bg_pairs.append(pair_id)
            pair_id += 1

    bg = [
        curses.color_pair(11),
        curses.color_pair(12),
        curses.color_pair(13),
        curses.color_pair(14),
        curses.color_pair(15),
        curses.color_pair(21),
        curses.color_pair(23),
        curses.color_pair(24),
        curses.color_pair(25),
        curses.color_pair(26),
        curses.color_pair(27),
        curses.color_pair(28),
        curses.color_pair(29),
        curses.color_pair(30),
        curses.color_pair(31),
        curses.color_pair(32),
        curses.color_pair(33),
    ]
    for pid in extra_bg_pairs:
        bg.append(curses.color_pair(pid))
    header = [curses.color_pair(16) | curses.A_BOLD, curses.color_pair(17) | curses.A_BOLD, curses.color_pair(20) | curses.A_BOLD]
    menu_hot = [curses.color_pair(19) | curses.A_BOLD, curses.color_pair(18) | curses.A_BOLD, curses.color_pair(17) | curses.A_BOLD]
    menu_dim = curses.color_pair(21)
    divider = curses.color_pair(22) | curses.A_BOLD
    text = curses.color_pair(21)
    ok = curses.color_pair(18) | curses.A_BOLD
    warn = curses.color_pair(19) | curses.A_BOLD
    info = curses.color_pair(17) | curses.A_BOLD

    return InsanePalette(bg=bg, header=header, menu_hot=menu_hot, menu_dim=menu_dim, divider=divider, text=text, ok=ok, warn=warn, info=info)


@dataclass
class ViewerState:
    title: str
    lines: List[str]
    top: int = 0


@dataclass
class EntropySource:
    # Material is not stored by default for photos; we store file path + hash.
    kind: str  # "photo" | "text" | "tap"
    name: str
    sha256: str
    size_bytes: int
    meta: Dict[str, object] = field(default_factory=dict)
    path: Optional[Path] = None
    text: Optional[str] = None


@dataclass
class AppState:
    theme: Theme
    insane: bool = False
    palette: Optional[InsanePalette] = None
    tick: int = 0
    godel_words: List[str] = field(default_factory=list)
    godel_phrase: str = ""
    godel_last_tick: int = 0
    entropy_sources: List[EntropySource] = field(default_factory=list)
    entropy_selected: int = 0
    entropy_min_sources: int = 7
    last_pack_dir: Optional[Path] = None
    last_out_dir: Optional[Path] = None
    last_input_dir: Optional[Path] = None
    # Use a stable, user-visible folder by default (inside the repo).
    drop_dir: Path = field(default_factory=lambda: _REPO_ROOT / "eps_drop")
    drop_seen: set[str] = field(default_factory=set)
    drop_last_count: int = 0
    drop_last_names: List[str] = field(default_factory=list)
    drop_flash_ticks: int = 0
    drop_paste_buf: str = ""
    drop_paste_last_ns: int = 0
    drop_import_count: int = 0
    drop_last_msgs: List[str] = field(default_factory=list)
    drop_last_msgs_ticks: int = 0
    focus: str = "menu"  # "menu" | "entropy"
    reward_ticks: int = 0
    menu: List[str] = field(
        default_factory=lambda: [
            "Entropy Sources",
            "Drop Zone",
            "Stamp Pack",
            "Verify Pack",
            "View README",
            "View TUI Standard",
            "View TUI Contract",
            "Quit",
        ]
    )
    selected: int = 0
    status: str = "Ready."
    log_lines: List[str] = field(default_factory=list)
    viewer: Optional[ViewerState] = None


def _divider_for_width(cols: int) -> str:
    return DIVIDER_WIDE if cols >= 80 else DIVIDER_NARROW


def _cycle(items: Sequence[int], tick: int, *, speed: int = 2, default: int = 0) -> int:
    if not items:
        return default
    idx = (int(tick) // max(1, int(speed))) % len(items)
    return int(items[idx])


def _fmt_bytes(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        return "?"
    units = ["B", "KiB", "MiB", "GiB"]
    v = float(n)
    for u in units:
        if v < 1024.0 or u == units[-1]:
            if u == "B":
                return f"{int(v)}{u}"
            return f"{v:.1f}{u}"
        v /= 1024.0
    return f"{n}B"


def _sha256_hex_path(path: Path, *, max_bytes: Optional[int] = None) -> Tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            n += len(chunk)
            if max_bytes is not None and n > int(max_bytes):
                raise ValueError(f"file exceeded max_bytes ({n} > {max_bytes})")
            h.update(chunk)
    return h.hexdigest(), n


def _entropy_pool_sha256(sources: Sequence["EntropySource"]) -> str:
    """
    Deterministic fingerprint of the *set* of staged sources (order-independent).
    Only uses non-sensitive metadata (hashes and kinds), not raw materials.
    """
    h = hashlib.sha256()
    parts: List[bytes] = []
    for s in sources:
        parts.append(f"{s.kind}:{s.sha256}:{int(s.size_bytes)}".encode("utf-8"))
    for p in sorted(parts):
        h.update(p)
        h.update(b"\n")
    return h.hexdigest()


_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in _IMG_EXTS


def _image_ascii_cached(path: Path, *, cols: int = 72, rows: int = 28) -> List[str]:
    """
    Convert an image into a small grayscale ASCII preview using ImageMagick.
    Cached by content hash + geometry so browsing in the TUI is fast.
    """
    magick = shutil.which("magick")
    if not magick:
        return [f"(no magick) {path.name}"]

    try:
        sha, _n = _sha256_hex_path(path, max_bytes=25 * 1024 * 1024)
    except Exception:
        sha = hashlib.sha256(path.as_posix().encode("utf-8")).hexdigest()

    cache_dir = Path(tempfile.gettempdir()) / "eps_img_cache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    cache_path = cache_dir / f"{sha}_{int(cols)}x{int(rows)}.txt"
    if cache_path.is_file():
        try:
            return cache_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            pass

    # Render to a tiny binary PGM and map pixels to chars.
    cmd = [
        magick,
        str(path),
        "-auto-orient",
        "-resize",
        f"{int(cols)}x{int(rows)}!",
        "-colorspace",
        "Gray",
        "-depth",
        "8",
        "pgm:-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False, timeout=15)
    except Exception as exc:
        return [f"preview failed: {exc}"]
    if proc.returncode != 0 or not proc.stdout:
        return [f"preview failed (magick rc={proc.returncode})"]

    data = proc.stdout
    # Parse PGM P5 header.
    if not data.startswith(b"P5"):
        return [f"preview failed (unexpected format)"]
    try:
        # Header is ascii tokens separated by whitespace; comments start with '#'.
        rest = data[2:]
        tokens: List[bytes] = []
        i = 0
        while len(tokens) < 3 and i < len(rest):
            # Skip whitespace
            while i < len(rest) and rest[i] in b" \t\r\n":
                i += 1
            if i >= len(rest):
                break
            if rest[i] == 35:  # '#'
                while i < len(rest) and rest[i] != 10:
                    i += 1
                continue
            j = i
            while j < len(rest) and rest[j] not in b" \t\r\n":
                j += 1
            tokens.append(rest[i:j])
            i = j
        w = int(tokens[0])
        h = int(tokens[1])
        _maxv = int(tokens[2])
        # Skip single whitespace char after maxv.
        while i < len(rest) and rest[i] in b" \t\r\n":
            i += 1
        pixels = rest[i : i + (w * h)]
        if len(pixels) < w * h:
            return [f"preview failed (short pixel data)"]
    except Exception:
        return [f"preview failed (bad pgm)"]

    ramp = " .,:;irsXA253hMHGS#9B&@"  # dense ramp
    lines: List[str] = []
    for y in range(h):
        row = pixels[y * w : (y + 1) * w]
        out = []
        for b in row:
            idx = int(b) * (len(ramp) - 1) // 255
            out.append(ramp[idx])
        lines.append("".join(out))
    try:
        cache_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
    return lines


def _clean_dropped_path(s: str) -> str:
    """
    Terminals on macOS often paste dropped paths either quoted or with backslash-escapes.
    Normalize the common forms.
    """
    v = (s or "").strip()
    if not v:
        return ""
    if v.startswith("file://"):
        # Finder may paste file:// URLs in some contexts.
        v = v[len("file://") :]
        try:
            v = urllib.parse.unquote(v)
        except Exception:
            pass
    # Strip matching quotes.
    if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
        v = v[1:-1]
    # Unescape common backslash escapes for spaces.
    v = v.replace("\\ ", " ")
    return v.strip()


def _split_drop_payload(s: str) -> List[str]:
    """
    Some terminals paste multiple drop paths separated by whitespace (or newlines).
    Return cleaned, non-empty entries (no cap).
    """
    raw = (s or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    # Normalize to whitespace and parse like a shell would (handles quotes and backslash escapes).
    raw_ws = raw.replace("\n", " ")
    parts: List[str]
    try:
        parts = shlex.split(raw_ws, posix=True)
    except Exception:
        parts = [p for p in raw.split("\n") if p.strip()]
    out: List[str] = []
    for p in parts:
        c = _clean_dropped_path(p)
        if c:
            out.append(c)
    return out


def _apply_drop_paths(state: AppState, paths: Sequence[str], *, max_apply: Optional[int] = None) -> List[str]:
    """
    Apply dropped paths: set default input dir and/or import entropy sources.
    Returns log lines for what happened.
    """
    msgs: List[str] = []
    for idx, v in enumerate(paths):
        if max_apply is not None and idx >= int(max_apply):
            msgs.append(f"Rejected (limit {int(max_apply)} per burst): {v}")
            continue
        p = Path(v).expanduser()
        if p.exists() and p.is_dir():
            state.last_input_dir = p.resolve()
            msgs.append(f"Input dir set: {state.last_input_dir}")
            continue
        if p.exists() and p.is_file() and _is_image_path(p):
            try:
                sha, size = _sha256_hex_path(p, max_bytes=100 * 1024 * 1024)
                dims = _identify_image_dims(p)
                meta: Dict[str, object] = {}
                if dims:
                    meta["dims"] = dims
                state.entropy_sources.append(
                    EntropySource(kind="photo", name=p.name, sha256=sha, size_bytes=size, meta=meta, path=p)
                )
                msgs.append(f"Photo source added: {p.name}")
                continue
            except Exception as exc:
                msgs.append(f"Photo add failed: {p.name}: {exc}")
                continue
        if p.exists() and p.is_file() and p.suffix.lower() in (".txt", ".md", ".markdown"):
            try:
                data = p.read_bytes()
                if len(data) > 2_000_000:
                    data = data[:2_000_000]
                txt = data.decode("utf-8", errors="ignore")
                sha = hashlib.sha256(txt.encode("utf-8", errors="ignore")).hexdigest()
                state.entropy_sources.append(
                    EntropySource(kind="text", name=p.name, sha256=sha, size_bytes=len(txt.encode("utf-8")), text=txt)
                )
                msgs.append(f"Text source added: {p.name}")
                continue
            except Exception as exc:
                msgs.append(f"Text add failed: {p.name}: {exc}")
                continue
        msgs.append(f"Not usable: {p}")

    if msgs:
        # Keep selection in bounds if list grew.
        if state.entropy_sources:
            state.entropy_selected = max(0, min(state.entropy_selected, len(state.entropy_sources) - 1))
    return msgs


def _count_drop_success(msgs: Sequence[str]) -> int:
    n = 0
    for m in msgs:
        ml = (m or "").lower()
        if ml.startswith("photo source added:") or ml.startswith("text source added:") or ml.startswith("input dir set:"):
            n += 1
    return n


def _dropzone_preview(state: AppState, *, width: int, height: int) -> List[str]:
    d = state.drop_dir
    have = len(state.drop_seen)
    lines: List[str] = []
    lines.append("DROP ZONE // human affordances for a chaotic TUI")
    lines.append("")
    lines.append("1) Terminal drag-drop (best effort):")
    lines.append("   Press Enter to open a big path field, then drag a folder/file into the terminal.")
    lines.append("   Most macOS terminals will paste the absolute path for you.")
    lines.append("   First 7 paths per burst are processed; extras are rejected.")
    lines.append("")
    lines.append("2) Finder drop folder (deterministic):")
    lines.append(f"   Drop items into: {d.resolve() if d.exists() else d}")
    lines.append("   EPS will auto-detect new items and import them.")
    lines.append(f"   Items currently in folder: {state.drop_last_count}")
    if state.drop_last_names:
        # Show a few to validate the user dropped into the right place.
        sample = ", ".join(state.drop_last_names[:5])
        lines.append(f"   Recent items: {sample}")
    lines.append("")
    lines.append("Auto-import rules:")
    lines.append("- Dropped directory: sets default Stamp input dir")
    lines.append("- Dropped image file: added as Entropy Source (photo)")
    lines.append("- Dropped .txt/.md: added as Entropy Source (text)")
    lines.append("")
    if state.last_input_dir is not None:
        lines.append(f"Current default input dir: {state.last_input_dir}")
    lines.append(f"Drop imports this run: {state.drop_import_count}")
    lines.append(f"Seen drop items this run: {have}")
    if state.drop_paste_buf:
        buf = state.drop_paste_buf.replace("\n", " ")[: max(0, width - 20)]
        lines.append(f"Buffered paste: {buf}")
    if state.drop_last_msgs and state.drop_last_msgs_ticks > 0:
        lines.append("")
        lines.append("Last import:")
        for m in state.drop_last_msgs[:3]:
            lines.append(f"- {m}")
    lines.append("Tip: Use Tab/Up/Down to navigate; q jumps to Quit, Enter quits.")

    # Draw a big landing box.
    box_w = max(12, min(width - 2, 78))
    box_h = max(6, min(height - len(lines) - 1, 12))
    if box_h >= 6 and box_w >= 20:
        lines.append("")
        top = "+" + ("-" * (box_w - 2)) + "+"
        mid = "|" + (" " * (box_w - 2)) + "|"
        bot = "+" + ("-" * (box_w - 2)) + "+"
        lines.append(top)
        for _ in range(box_h - 2):
            lines.append(mid)
        lines.append(bot)
        msg = "IMPORTED" if state.drop_flash_ticks > 0 else "DROP HERE (paste/drag)"
        if len(msg) < box_w - 2:
            pad = (box_w - 2 - len(msg)) // 2
            msg_line = "|" + (" " * pad) + msg + (" " * (box_w - 2 - pad - len(msg))) + "|"
            # Put message near the center.
            insert_at = len(lines) - (box_h // 2) - 1
            if 0 <= insert_at < len(lines):
                lines[insert_at] = msg_line
    return [ln[:width] for ln in lines[:height]]


def _poll_drop_dir(state: AppState) -> None:
    """
    Best-effort "drag and drop" via a filesystem landing zone.
    Users can drop items into `state.drop_dir` (Finder can access /tmp via Go to Folder).
    """
    d = state.drop_dir
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    try:
        items = sorted(d.iterdir(), key=lambda p: p.name)
    except Exception:
        return

    # Telemetry for the Drop Zone screen so users can see whether they're dropping into the right folder.
    try:
        names = [p.name for p in items if p.name not in (".DS_Store",)]
    except Exception:
        names = []
    state.drop_last_count = len(names)
    state.drop_last_names = names[:10]

    changed = False
    imported_this_poll = 0
    for p in items:
        if p.name in (".DS_Store",):
            continue
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in state.drop_seen:
            continue
        state.drop_seen.add(key)

        msgs = _apply_drop_paths(state, [str(p)])
        ok = _count_drop_success(msgs)
        if ok > 0:
            state.drop_import_count += ok
            state.drop_last_msgs = msgs[:3]
            state.drop_last_msgs_ticks = 40
            state.drop_flash_ticks = 10
            changed = True
            imported_this_poll += ok
            # Keep the UI responsive if the user drops a ton of files.
            if imported_this_poll >= 7:
                # Reject the rest of this poll burst so they do not get processed on later ticks.
                rejected = 0
                for p2 in items:
                    if p2.name in (".DS_Store",):
                        continue
                    try:
                        key2 = str(p2.resolve())
                    except Exception:
                        key2 = str(p2)
                    if key2 in state.drop_seen:
                        continue
                    state.drop_seen.add(key2)
                    rejected += 1
                if rejected > 0:
                    state.drop_last_msgs = [f"Rejected extras (limit 7 per burst): {rejected}"]
                    state.drop_last_msgs_ticks = 40
                break

    if changed:
        # Keep selection in bounds if list grew/shrank.
        if state.entropy_sources:
            state.entropy_selected = max(0, min(state.entropy_selected, len(state.entropy_sources) - 1))


def _load_wordlist_from_text_file(path: Path, *, max_bytes: int = 5_000_000) -> List[str]:
    """
    Best-effort word extraction for the insane header. This intentionally does not preserve
    punctuation; it exists only to generate short flashing tags.
    """
    try:
        data = path.read_bytes()
    except Exception:
        return []
    if len(data) > int(max_bytes):
        data = data[: int(max_bytes)]
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return []
    # Accept common Latin letters including diacritics (covers "Gödel" in many encodings).
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", text)
    vowels = set("aeiouyAEIOUYäöüÄÖÜ")

    def ok(w: str) -> bool:
        if not (3 <= len(w) <= 22):
            return False
        if not any(c in vowels for c in w):
            return False
        # Reject low-information runs like "SSSS" or "IIII".
        if len(set(w)) <= 1:
            return False
        return True

    out = [w for w in words if ok(w)]
    return out


_EN_WORDS: Optional[set[str]] = None


def _load_english_words() -> set[str]:
    """
    Best-effort English dictionary set from macOS word lists.
    This is intentionally light: it exists only to filter OCR garbage in the insane header.
    """
    global _EN_WORDS
    if _EN_WORDS is not None:
        return _EN_WORDS
    candidates = [
        Path("/Library/Spelling/web2"),
        Path("/Library/Spelling/words"),
        Path("/usr/share/dict/words"),
    ]
    words: set[str] = set()
    for p in candidates:
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in raw.splitlines():
            w = line.strip()
            if not w or w.startswith("#"):
                continue
            if "'" in w or "-" in w:
                continue
            if not w.isalpha():
                continue
            if 3 <= len(w) <= 22:
                words.add(w.lower())
        if len(words) >= 50_000:
            break
    _EN_WORDS = words
    return words


_DE_STOP = {
    "der",
    "die",
    "das",
    "und",
    "nicht",
    "ist",
    "sein",
    "sind",
    "mit",
    "für",
    "auf",
    "aus",
    "als",
    "auch",
    "eine",
    "einer",
    "eines",
    "dem",
    "den",
    "des",
    "im",
    "in",
    "zu",
    "von",
    "oder",
    "dass",
}

_EN_SHORT_OK = {
    # Allow a small set of common short words; 3-letter OCR tokens are otherwise too noisy.
    "and",
    "are",
    "but",
    "can",
    "for",
    "not",
    "set",
    "the",
    "was",
    "with",
}


def _looks_german(word: str) -> bool:
    w = word.lower()
    if w in _DE_STOP:
        return True
    if any(c in w for c in ("ä", "ö", "ü", "ß")):
        return True
    # Common German morphological tails.
    suffixes = (
        "ung",
        "keit",
        "heit",
        "lich",
        "isch",
        "schaft",
        "tion",
        "ismus",
        "ieren",
        "chen",
        "lein",
    )
    if len(w) >= 6 and w.endswith(suffixes):
        return True
    # A few high-signal trigrams.
    if "sch" in w and len(w) >= 5:
        return True
    return False


def _filter_words_en_de(words: List[str]) -> List[str]:
    en = _load_english_words()
    out: List[str] = []
    seen: set[str] = set()
    for w in words:
        wl = w.lower()
        is_en = wl in en
        is_de = _looks_german(w)
        if not (is_en or is_de):
            continue
        # Guardrail: 3-letter OCR tokens are often fragments; only keep a tiny allowlist.
        if len(wl) == 3 and is_en and wl not in _EN_SHORT_OK:
            continue
        if len(wl) < 3 or len(wl) > 22:
            continue
        if wl in seen:
            continue
        seen.add(wl)
        # Header rendering prefers stable lowercase to avoid OCR SHOUTING.
        out.append(wl)
    return out


def _load_wordlist_from_source(path: Path, *, max_bytes: int = 5_000_000) -> List[str]:
    """
    Load words from text/markdown sources.
    PDF runtime extraction is intentionally disabled to avoid repeated OCR/PDF dependency.
    """
    suffix = path.suffix.lower()
    if suffix != ".pdf":
        return _load_wordlist_from_text_file(path, max_bytes=max_bytes)
    return []


def _resolve_godel_source(path_s: str) -> Optional[Path]:
    """
    Accept either a file path or a directory.
    - If a file: use it.
    - If a dir: find a likely text/markdown file with 'godel'/'gödel' in the name.
    """
    if not path_s:
        return None
    p = Path(path_s).expanduser()
    if not p.exists():
        return None
    if p.is_file():
        return p
    if not p.is_dir():
        return None

    exts = {".txt", ".md", ".markdown", ".pdf"}
    candidates: List[Path] = []
    # Keep it bounded; this is used at app start.
    for fp in p.rglob("*"):
        try:
            if not fp.is_file():
                continue
        except OSError:
            continue
        if fp.suffix.lower() not in exts:
            continue
        name_l = fp.name.lower()
        if "godel" not in name_l and "gödel" not in name_l:
            continue
        candidates.append(fp)
        if len(candidates) >= 50:
            break

    if not candidates:
        return None

    def score(fp: Path) -> int:
        s = 0
        name_l = fp.name.lower()
        if fp.suffix.lower() == ".txt":
            s += 10
        if fp.suffix.lower() == ".pdf":
            s += 8
        if "set" in name_l or "sets" in name_l or "theory" in name_l:
            s += 5
        try:
            size = int(fp.stat().st_size)
        except OSError:
            size = 0
        # Prefer something non-trivial but not huge.
        if size >= 10_000:
            s += 3
        if size >= 100_000:
            s += 1
        return s

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def _load_bundled_godel_words() -> List[str]:
    try:
        p = Path(BUNDLED_GODEL_WORDS)
        if not p.is_file():
            return []
    except OSError:
        return []
    words = _load_wordlist_from_text_file(Path(BUNDLED_GODEL_WORDS), max_bytes=1_000_000)
    out: List[str] = []
    seen: set[str] = set()
    for w in words:
        wl = str(w).strip().lower()
        if not wl or wl in seen:
            continue
        seen.add(wl)
        out.append(wl)
    return out


def _update_godel_phrase(state: AppState, *, min_interval_ticks: int = 6) -> None:
    if not state.godel_words:
        return
    if state.tick - state.godel_last_tick < int(min_interval_ticks):
        return
    state.godel_last_tick = state.tick

    # Pseudo-random selection driven by monotonic time; "insane" mode is allowed to be non-deterministic.
    t = time.monotonic_ns()
    n_words = 1 + int((t >> 7) % 3)  # 1..3
    start = int((t ^ (state.tick << 16)) % len(state.godel_words))
    chosen: List[str] = []
    for i in range(n_words):
        chosen.append(state.godel_words[(start + i) % len(state.godel_words)])
    state.godel_phrase = " ".join(chosen)


def _read_text_lines(path: Path, limit: int = 2000) -> List[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"error: failed to read {path}: {exc}"]
    return raw.splitlines()[:limit]


def open_viewer(state: AppState, title: str, lines: List[str]) -> None:
    state.viewer = ViewerState(title=title, lines=lines, top=0)
    state.status = ""


def close_viewer(state: AppState) -> None:
    state.viewer = None
    state.status = "Ready."


def _draw_header(stdscr, state: AppState, cols: int) -> None:
    safe_addstr(stdscr, 0, 0, f" {APP_NAME} {APP_VERSION}"[:cols].ljust(cols), state.theme.header)

    # Keep semantics simple and monotone-safe.
    mode = "offline"
    s = (state.status or "").strip().lower()
    if "fail" in s:
        risk = "WARN"
    elif "done" in s:
        risk = "OK"
    else:
        risk = "INFO"
    action = "none"
    status_line = f"MODE: {mode}  RISK: {risk}  ACTION: {action}"
    safe_addstr(stdscr, 1, 0, status_line[:cols].ljust(cols), state.theme.normal)

    divider = _divider_for_width(cols)
    safe_addstr(stdscr, 2, 0, divider[:cols].ljust(cols), state.theme.normal)


def _draw_insane_background(stdscr, state: AppState, rows: int, cols: int) -> None:
    if state.palette is None:
        return
    # Glitch stripes: horizontal bands plus shifting vertical segments.
    bg = state.palette.bg
    if not bg:
        return

    seg = 26 if cols >= 160 else (22 if cols >= 140 else (18 if cols >= 120 else 12))
    wobble = 11 + ((state.tick // 11) % 29)  # longer loop
    direction = 1 if ((state.tick // 60) % 2 == 0) else -1
    ch_bank = [" ", "░", "▒", "▓"]
    reward = state.reward_ticks > 0
    drop_flash = state.drop_flash_ticks > 0

    for y in range(rows):
        # Big horizontal banding (moves up/down over time).
        band = (y + direction * (state.tick // 2)) // max(1, wobble)
        # Per-row seed for vertical segmentation.
        row_seed = (state.tick * 5 + band * 17 + y * 3) & 0xFFFFFFFF

        x = 0
        while x < cols:
            # Per-segment jitter changes width slightly.
            jitter = ((row_seed >> (x % 13)) & 0x3) - 1  # -1..2
            run = max(6, min(seg + jitter, cols - x))
            idx = (band + (x // seg) + ((row_seed >> 8) & 0xF)) % len(bg)
            # Occasionally fill with grain characters instead of spaces to amplify the "video noise" look.
            ch = ch_bank[(row_seed >> (x % 17)) & 0x3]
            if reward and ((row_seed >> (x % 11)) & 0x7) == 0:
                ch = "█"
            if drop_flash and (y % 11 == 0) and (x % 19 == 0):
                ch = " "
            safe_addstr(stdscr, y, x, (ch * run), bg[idx])
            x += run

        # Occasional tear bars.
        if (state.tick + y) % 31 == 0 and cols >= 16:
            tear_w = min(cols, 12 + ((row_seed >> 3) % 50))
            tear_attr = bg[(band + 7) % len(bg)]
            safe_addstr(stdscr, y, 0, ("▓" * tear_w), tear_attr)
        # Sparkle noise: a few high-contrast pixels that "crawl".
        if (row_seed % (3 if reward else 7)) == 0 and cols >= 6:
            sx = int((row_seed >> 9) % max(1, cols - 1))
            ch = "█" if (row_seed & 1) else "▒"
            safe_addstr(stdscr, y, sx, ch, bg[(band + 3) % len(bg)] | curses.A_BOLD)
        # Vertical scanlines: small high-frequency jitter overlay.
        if cols >= 40 and (row_seed & 0x1) == 0:
            step = 3 if cols >= 120 else 4
            for sx in range((row_seed >> 5) % step, cols, step):
                attr = bg[(band + (sx // step) + ((row_seed >> 11) & 0x7)) % len(bg)]
                safe_addstr(stdscr, y, sx, " ", attr | (curses.A_BOLD if (row_seed >> (sx % 9)) & 1 else 0))


def _write_modulated_sine_wav(
    wav_path: Path,
    *,
    duration_s: float = 3.0,
    hold_hz: float = 25.0,
    f_min_hz: float = 100.0,
    f_max_hz: float = 1000.0,
    sample_rate: int = 44100,
) -> None:
    """
    Generate a 16-bit PCM mono WAV:
    - sample-and-hold frequency updates at hold_hz (default 25 Hz)
    - random frequency per hold in [f_min_hz, f_max_hz]
    - duration_s seconds total
    """
    rng = random.SystemRandom()
    n_segs = max(1, int(round(float(duration_s) * float(hold_hz))))
    seg_len = max(1, int(sample_rate // max(1.0, float(hold_hz))))
    amp = 0.22
    phase = 0.0

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        out = bytearray()
        for _ in range(n_segs):
            f = float(rng.uniform(float(f_min_hz), float(f_max_hz)))
            w = (2.0 * math.pi * f) / float(sample_rate)
            for _i in range(seg_len):
                phase += w
                s = int(amp * 32767.0 * math.sin(phase))
                out += int(s).to_bytes(2, "little", signed=True)
        wf.writeframes(out)


def _start_supernova_sfx_best_effort(*, duration_s: float = 5.0) -> None:
    """
    Best-effort audio effect for macOS (afplay). Non-fatal if unavailable.
    Runs async to avoid blocking the TUI.
    """
    if shutil.which("afplay") is None:
        return

    tmp_dir = Path(tempfile.gettempdir())
    wav_path = tmp_dir / f"eps_supernova_{os.getpid()}_{int(time.time())}.wav"
    dur = max(0.5, min(float(duration_s), 10.0))

    def _worker() -> None:
        try:
            _write_modulated_sine_wav(wav_path, duration_s=dur, hold_hz=25.0, f_min_hz=100.0, f_max_hz=1000.0)
            p = subprocess.Popen(
                ["afplay", str(wav_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                p.wait(timeout=10)
            except Exception:
                pass
        except Exception:
            pass
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass

    t = threading.Thread(target=_worker, name="eps_supernova_sfx", daemon=True)
    t.start()


def _fx_no_entropy(stdscr, state: AppState, *, duration_s: float = 5.0, fps: int = 25) -> None:
    """
    Error payoff: flash red with a skull/crossbones + admonition.
    Only used in insane mode.
    """
    if not state.insane:
        return

    rows, cols = stdscr.getmaxyx()
    if rows < 12 or cols < 50:
        return

    # Try to allocate a dedicated "panic red" pair.
    attr_red = curses.A_REVERSE | curses.A_BOLD
    if curses.has_colors():
        try:
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                pass
            is_256 = getattr(curses, "COLORS", 0) >= 256
            bg_red = 196 if is_256 else curses.COLOR_RED
            fg = 231 if is_256 else curses.COLOR_WHITE
            _init_pair_safe(70, int(fg), int(bg_red))
            attr_red = curses.color_pair(70) | curses.A_BOLD
        except curses.error:
            pass

    skull = [
        r"      .-''''-.",
        r"    .'  _  _  '.",
        r"   /   (o)(o)   \ ",
        r"  :             :",
        r"  |    \___/    |",
        r"  :   .'---'.   :",
        r"   \  '-----'  / ",
        r"    '.       .'",
        r"      '-.__.-'",
    ]
    bones = [
        r" \\/           \\/ ",
        r" /\\           /\\ ",
    ]
    msg1 = "NO STAMPING WITHOUT ENTROPY"
    msg2 = "Add entropy sources first (photos/text/tap or Drop Zone)."

    frames = max(1, int(round(float(duration_s) * int(fps))))
    cx = cols // 2
    cy = rows // 2

    try:
        stdscr.nodelay(True)
        stdscr.timeout(0)
    except curses.error:
        pass

    for f in range(frames):
        stdscr.erase()

        # Flashing background (red / black-red).
        flash = (f // 3) % 2 == 0
        bg_attr = attr_red | (curses.A_BLINK if flash else 0)
        for y in range(rows):
            safe_addstr(stdscr, y, 0, (" " * cols), bg_attr)

        # Draw bones behind skull.
        by = max(0, cy - len(skull) // 2 - 1)
        for i, line in enumerate(bones):
            y = by + i
            if 0 <= y < rows:
                x = max(0, cx - len(line) // 2)
                safe_addstr(stdscr, y, x, line[: max(0, cols - x)], bg_attr)

        # Draw skull.
        sy = max(0, cy - len(skull) // 2)
        for i, line in enumerate(skull):
            y = sy + i
            if 0 <= y < rows:
                x = max(0, cx - len(line) // 2)
                safe_addstr(stdscr, y, x, line[: max(0, cols - x)], bg_attr)

        # Messages.
        safe_addstr(stdscr, min(rows - 2, sy + len(skull) + 1), max(0, cx - len(msg1) // 2), msg1[:cols], bg_attr | curses.A_BOLD)
        safe_addstr(stdscr, min(rows - 1, sy + len(skull) + 2), max(0, cx - len(msg2) // 2), msg2[:cols], bg_attr)

        stdscr.refresh()
        time.sleep(max(0.0, 1.0 / float(fps)))

    try:
        stdscr.nodelay(False)
        stdscr.timeout(50 if state.insane else 100)
    except curses.error:
        pass


def _fx_kaleidoscope(
    stdscr,
    state: AppState,
    *,
    center_text: str,
    duration_s: float,
    fps: int = 25,
    allow_skip: bool = True,
) -> None:
    """
    Psychedelic kaleidoscopic burst for a fixed duration.
    """
    if not state.insane or state.palette is None:
        return

    rows, cols = stdscr.getmaxyx()
    if rows < 10 or cols < 40:
        return

    rng = random.SystemRandom()
    max_r = max(6, min(rows // 2 - 2, cols // 2 - 4))
    cx = cols // 2
    cy = rows // 2
    frames = max(1, int(round(float(duration_s) * int(fps))))

    try:
        stdscr.nodelay(True)
        stdscr.timeout(0)
    except curses.error:
        pass

    chars = ["██", "▓▓", "▒▒", "░░", "##", "[]", "<>"]
    colors = list(state.palette.bg) + list(state.palette.header) + list(state.palette.menu_hot)
    if not colors:
        colors = [state.palette.text]

    for f in range(frames):
        t = float(f) / float(max(1, frames - 1))
        e = (t * t) * (3.0 - 2.0 * t)
        r = int(e * max_r)

        stdscr.erase()

        spark_n = 140 if cols >= 140 else 90
        for _ in range(spark_n):
            sx = int(rng.randrange(0, cols - 2))
            sy = int(rng.randrange(0, rows))
            ch = chars[int(rng.randrange(0, len(chars)))]
            attr = colors[int(rng.randrange(0, len(colors)))]
            if rng.randrange(0, 9) == 0:
                attr |= curses.A_BOLD
            safe_addstr(stdscr, sy, sx, ch[: max(0, cols - sx)], attr)

        rings = 5
        pts = 64 if cols >= 140 else 48
        spin = (t * 2.0 * math.pi) * (1.0 + (rng.random() * 0.2))
        for ri in range(1, rings + 1):
            rr = int((r * ri) / rings)
            if rr <= 0:
                continue
            jitter = (rng.random() - 0.5) * 0.25
            for k in range(pts):
                a = (2.0 * math.pi * float(k) / float(pts)) + spin + jitter
                dx = int(round(float(rr) * math.cos(a)))
                dy = int(round(float(rr) * math.sin(a)))
                for sx, sy in ((dx, dy), (-dx, dy), (dx, -dy), (-dx, -dy), (dy, dx), (-dy, dx), (dy, -dx), (-dy, -dx)):
                    x = cx + int(sx)
                    y = cy + int(sy)
                    if y < 0 or y >= rows or x < 0 or x >= cols - 1:
                        continue
                    ch = chars[(k + ri + f) % len(chars)]
                    attr = colors[(k + ri * 7 + f * 3) % len(colors)]
                    if ((k + f) % 11) == 0:
                        attr |= curses.A_BOLD
                    if ((k + ri + f) % 17) == 0:
                        attr |= curses.A_BLINK
                    safe_addstr(stdscr, y, x, ch[: max(0, cols - x)], attr)

        core_attr = colors[(f * 5) % len(colors)] | curses.A_BOLD
        safe_addstr(stdscr, cy, max(0, cx - len(center_text) // 2), center_text[:cols], core_attr)

        stdscr.refresh()

        if allow_skip:
            try:
                ch = stdscr.getch()
            except curses.error:
                ch = -1
            if ch in (27, ord("q")):
                break

        time.sleep(max(0.0, (1.0 / float(fps))))

    try:
        stdscr.nodelay(False)
        stdscr.timeout(50 if state.insane else 100)
    except curses.error:
        pass


def _fx_supernova(stdscr, state: AppState, *, duration_s: float = 3.0, fps: int = 25) -> None:
    """
    Stamp-complete visual: psychedelic kaleidoscopic supernova.

    Intentional baseline violation; only runs in insane mode.
    """
    if not state.insane or state.palette is None:
        return
    _start_supernova_sfx_best_effort(duration_s=float(duration_s))
    _fx_kaleidoscope(stdscr, state, center_text="STAMPING ENTROPY", duration_s=float(duration_s), fps=int(fps))


def _stamp_with_insane_fx(stdscr, state: AppState, fn, *, min_stamping_s: float = 6.0, created_s: float = 5.0) -> object:
    """
    Run fn() (stamp) while showing a minimum-duration "STAMPING ENTROPY" kaleidoscope,
    then show a fixed-duration "ENTROPY PACK CREATED" end burst.
    """
    holder: Dict[str, object] = {"res": None, "exc": None}

    def _worker() -> None:
        try:
            holder["res"] = fn()
        except Exception as exc:
            holder["exc"] = exc

    t0 = time.monotonic()
    th = threading.Thread(target=_worker, name="eps_stamp_worker", daemon=True)
    th.start()

    # Phase 1: animate for a fixed minimum time. (Stamp may complete sooner or later.)
    _start_supernova_sfx_best_effort(duration_s=float(min_stamping_s))
    if state.palette is not None:
        rows, cols = stdscr.getmaxyx()
        rng = random.SystemRandom()
        max_r = max(6, min(rows // 2 - 2, cols // 2 - 4))
        cx = cols // 2
        cy = rows // 2
        chars = ["██", "▓▓", "▒▒", "░░", "##", "[]", "<>"]
        colors = list(state.palette.bg) + list(state.palette.header) + list(state.palette.menu_hot)
        if not colors:
            colors = [state.palette.text]

        try:
            stdscr.nodelay(True)
            stdscr.timeout(0)
        except curses.error:
            pass

        fps_i = 25
        cycle_s = 1.25
        phase1_end = t0 + float(min_stamping_s)
        frame = 0
        while True:
            now = time.monotonic()
            elapsed = max(0.0, now - t0)
            if now >= phase1_end:
                break
            # Looping burst: expand to max repeatedly while stamping runs.
            t_cycle = (elapsed % cycle_s) / cycle_s
            e = (t_cycle * t_cycle) * (3.0 - 2.0 * t_cycle)
            r = int(e * max_r)

            stdscr.erase()

            spark_n = 140 if cols >= 140 else 90
            for _ in range(spark_n):
                sx = int(rng.randrange(0, max(1, cols - 2)))
                sy = int(rng.randrange(0, max(1, rows)))
                ch = chars[int(rng.randrange(0, len(chars)))]
                attr = colors[int(rng.randrange(0, len(colors)))]
                if rng.randrange(0, 9) == 0:
                    attr |= curses.A_BOLD
                safe_addstr(stdscr, sy, sx, ch[: max(0, cols - sx)], attr)

            rings = 5
            pts = 64 if cols >= 140 else 48
            spin = (elapsed * 2.0 * math.pi * 0.35) + (frame * 0.07)
            for ri in range(1, rings + 1):
                rr = int((r * ri) / rings)
                if rr <= 0:
                    continue
                jitter = (rng.random() - 0.5) * 0.25
                for k in range(pts):
                    a = (2.0 * math.pi * float(k) / float(pts)) + spin + jitter
                    dx = int(round(float(rr) * math.cos(a)))
                    dy = int(round(float(rr) * math.sin(a)))
                    for sx, sy in ((dx, dy), (-dx, dy), (dx, -dy), (-dx, -dy), (dy, dx), (-dy, dx), (dy, -dx), (-dy, -dx)):
                        x = cx + int(sx)
                        y = cy + int(sy)
                        if y < 0 or y >= rows or x < 0 or x >= cols - 1:
                            continue
                        ch = chars[(k + ri + frame) % len(chars)]
                        attr = colors[(k + ri * 7 + frame * 3) % len(colors)]
                        if ((k + frame) % 11) == 0:
                            attr |= curses.A_BOLD
                        if ((k + ri + frame) % 17) == 0:
                            attr |= curses.A_BLINK
                        safe_addstr(stdscr, y, x, ch[: max(0, cols - x)], attr)

            core_attr = colors[(frame * 5) % len(colors)] | curses.A_BOLD
            safe_addstr(stdscr, cy, max(0, cx - len("STAMPING ENTROPY") // 2), "STAMPING ENTROPY"[:cols], core_attr)
            stdscr.refresh()

            if not th.is_alive() and holder.get("exc") is not None:
                break

            time.sleep(max(0.0, (1.0 / float(fps_i))))
            frame += 1

        try:
            stdscr.nodelay(False)
            stdscr.timeout(50 if state.insane else 100)
        except curses.error:
            pass

    # If stamping is still running after the fixed burst, show a calmer hold screen until completion.
    if th.is_alive():
        hold_frame = 0
        while th.is_alive():
            rows, cols = stdscr.getmaxyx()
            stdscr.erase()
            # Minimal but still loud.
            msg = "STAMPING... (working)"
            dots = "." * ((hold_frame // 5) % 4)
            line = (msg + dots).strip()
            attr = state.palette.warn if state.palette is not None else curses.A_REVERSE
            safe_addstr(stdscr, rows // 2, max(0, (cols - len(line)) // 2), line[:cols], attr | curses.A_BOLD)
            safe_addstr(stdscr, min(rows - 2, rows // 2 + 2), max(0, (cols - 34) // 2), "Tip: large input dirs can take time", attr)
            stdscr.refresh()
            time.sleep(0.1)
            hold_frame += 1

    th.join()
    if holder.get("exc") is not None:
        raise holder["exc"]  # type: ignore[misc]

    # Phase 2: end burst.
    _fx_kaleidoscope(stdscr, state, center_text="ENTROPY PACK CREATED", duration_s=float(created_s), fps=25, allow_skip=True)

    return holder["res"]


def _draw_insane_header(stdscr, state: AppState, cols: int) -> None:
    if state.palette is None:
        return

    head_attr = _cycle(state.palette.header, state.tick, speed=1, default=state.palette.text)
    safe_addstr(stdscr, 0, 0, (" " * cols), head_attr)

    _update_godel_phrase(state)
    phase = int(time.monotonic() * 8) % 4
    fallback = ["NEON", "RAVE", "GLITCH", "HOT"][phase]
    left_tag = state.godel_phrase or fallback

    left = f" {left_tag} "
    right = f"{APP_NAME} {APP_VERSION}"
    # Draw right first so it never gets overwritten by a long left phrase.
    rx = max(0, cols - len(right))
    safe_addstr(stdscr, 0, rx, right[: max(0, cols - rx)], head_attr)
    # If overlap, truncate left so it cannot collide with the right identity.
    max_left = max(0, rx - 1)
    safe_addstr(stdscr, 0, 0, left[:max_left].ljust(max_left), head_attr)

    s = (state.status or "").strip()
    s_l = s.lower()
    if "fail" in s_l:
        risk_attr = state.palette.warn
        risk = "WARN"
    elif "done" in s_l:
        risk_attr = state.palette.ok
        risk = "OK"
    else:
        risk_attr = state.palette.info
        risk = "INFO"
    meta = f" MODE=OFFLINE  RISK={risk}  TICK={state.tick}  STATUS={s or 'Ready'} "
    safe_addstr(stdscr, 1, 0, meta[:cols].ljust(cols), risk_attr)

    div = ("═" * max(0, cols - 2)) if cols >= 2 else ""
    safe_addstr(stdscr, 2, 0, ("╬" + div + "╬")[:cols].ljust(cols), state.palette.divider)


def _draw_insane_menu(stdscr, state: AppState, top: int, left_w: int, height: int) -> None:
    if state.palette is None:
        return
    for i in range(height):
        idx = i
        y = top + i
        if idx >= len(state.menu):
            safe_addstr(stdscr, y, 0, " " * left_w, state.palette.menu_dim)
            continue
        label = state.menu[idx]
        selected = idx == state.selected
        if selected and state.focus == "menu":
            attr = _cycle(state.palette.menu_hot, state.tick + idx, speed=2, default=state.palette.menu_dim)
        elif selected:
            attr = state.palette.menu_dim | curses.A_BOLD
        else:
            attr = state.palette.menu_dim
        prefix = ">> " if selected else "   "
        text = (prefix + label)[:left_w].ljust(left_w)
        safe_addstr(stdscr, y, 0, text, attr)


def _draw_insane_viewer(stdscr, state: AppState, top: int, cols: int, rows: int) -> None:
    if state.viewer is None or state.palette is None:
        return
    v = state.viewer
    body_h = rows - top - 1
    title_attr = _cycle(state.palette.header, state.tick, speed=2, default=state.palette.text)
    safe_addstr(stdscr, top, 0, (f"[VIEW] {v.title}")[:cols].ljust(cols), title_attr)
    for i in range(body_h - 1):
        src_idx = v.top + i
        y = top + 1 + i
        if src_idx >= len(v.lines):
            safe_addstr(stdscr, y, 0, " " * cols, state.palette.text)
            continue
        safe_addstr(stdscr, y, 0, v.lines[src_idx][:cols].ljust(cols), state.palette.text)


def _entropy_sources_preview(state: AppState, *, width: int, height: int) -> List[str]:
    """
    Right-pane content for the Entropy Sources screen.
    """
    min_n = int(state.entropy_min_sources)
    n = len(state.entropy_sources)
    pool = _entropy_pool_sha256(state.entropy_sources)[:16] if state.entropy_sources else "none"
    header = f"ENTROPY SOURCES // {n}/{min_n} required for LOCKDOWN seed  pool={pool}"
    lines: List[str] = [header, ""]
    focus = "LIST" if state.focus == "entropy" else "MENU"
    lines.append(f"FOCUS={focus}  Tab=toggle focus")
    lines.append("")
    lines.append("[A] Add Photos   [T] Add Text   [Space] Tap Entropy   [Enter] Preview   [D] Delete   [C] Clear")
    lines.append("")
    if not state.entropy_sources:
        lines.append("No sources staged.")
        lines.append("Add at least 7 sources, then enable derive seed in Stamp Pack.")
        return lines[:height]

    sel = max(0, min(int(state.entropy_selected), len(state.entropy_sources) - 1))
    for i, s in enumerate(state.entropy_sources[: max(0, height - 6)]):
        mark = ">>" if i == sel else "  "
        meta_bits: List[str] = []
        if s.kind == "photo":
            dims = s.meta.get("dims")
            if isinstance(dims, str) and dims:
                meta_bits.append(dims)
        if s.kind == "tap":
            cnt = s.meta.get("events")
            if isinstance(cnt, int):
                meta_bits.append(f"events={cnt}")
        meta = (" " + " ".join(meta_bits)) if meta_bits else ""
        short = s.sha256[:10]
        lines.append(f"{mark} [{s.kind}] {s.name}  {_fmt_bytes(s.size_bytes)}  sha={short}{meta}")
    return [ln[:width] for ln in lines[:height]]


def _draw_insane_right_pane(stdscr, state: AppState, top: int, left_w: int, cols: int, rows: int) -> None:
    if state.palette is None:
        return
    body_h = rows - top - 1
    right_x = left_w + 1
    right_w = max(0, cols - right_x)

    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    preview: List[str] = []
    if label == "Entropy Sources":
        preview = _entropy_sources_preview(state, width=right_w, height=body_h)
    elif label == "Drop Zone":
        preview = _dropzone_preview(state, width=right_w, height=body_h)
    elif label == "Stamp Pack":
        preview = [
            "STAMP // directory -> content-addressed pack",
            "",
            "Outputs:",
            "  manifest.json",
            "  entropy_root_sha256.txt",
            "  receipt.json (operational)",
            "  payload/...",
            "  entropy_pack.zip (optional)",
            "",
            "Seed (optional): HKDF(root) -> seed_master",
        ]
    elif label == "Verify Pack":
        preview = [
            "VERIFY // root + payload integrity",
            "",
            "Hardening:",
            "  caps (manifest/artifact/total)",
            "  traversal + symlink defense",
            "  zip duplicate member defense",
        ]
    elif label.startswith("View "):
        preview = ["Open a read-only viewer."]
    elif label == "Quit":
        preview = ["Exit."]

    if state.log_lines and label not in ("Drop Zone", "Entropy Sources"):
        preview = state.log_lines[-(body_h - 1) :]

    for i in range(body_h):
        y = top + i
        safe_addstr(stdscr, y, left_w, "║", state.palette.divider)

    for i in range(body_h):
        y = top + i
        line = preview[i] if i < len(preview) else ""
        if label == "Drop Zone":
            is_box = ("╔" in line) or ("╚" in line) or line.strip().startswith("║") or ("DROP HERE" in line) or ("IMPORTED" in line)
            if state.drop_flash_ticks > 0 and is_box:
                attr = state.palette.ok | curses.A_BOLD
            else:
                attr = state.palette.text
        else:
            attr = state.palette.text if i % 2 == 0 else _cycle(state.palette.bg, state.tick + i, speed=4, default=state.palette.text)
        safe_addstr(stdscr, y, right_x, line[:right_w].ljust(right_w), attr)


def _draw_footer(stdscr, state: AppState, rows: int, cols: int) -> None:
    if state.viewer is not None:
        legend = "Up/Down/PgUp/PgDn: scroll  Esc/q/Enter: back"
        msg = state.status.strip() if state.status else ""
        line = legend
        if msg:
            if len(line) + 2 + len(msg) <= cols:
                line = f"{legend}{' ' * (cols - len(legend) - len(msg))}{msg}"
            else:
                line = f"{legend}  {msg}"
        safe_addstr(stdscr, rows - 1, 0, line[:cols].ljust(cols), state.theme.normal)
        return

    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    if label == "Entropy Sources":
        legend = "Up/Down: move  Tab: focus  A/T/Space: add  Enter: preview  Esc: back"
    elif label == "Drop Zone":
        legend = "Up/Down: move  Enter: drop path  Esc: back"
    else:
        legend = "Up/Down: move  Enter: select  Esc: back"
    msg = state.status.strip() if state.status else ""
    line = legend
    if msg:
        # Right-align the status message when possible.
        if len(line) + 2 + len(msg) <= cols:
            line = f"{legend}{' ' * (cols - len(legend) - len(msg))}{msg}"
        else:
            line = f"{legend}  {msg}"
    safe_addstr(stdscr, rows - 1, 0, line[:cols].ljust(cols), state.theme.normal)


def _draw_menu(stdscr, state: AppState, top: int, left_w: int, height: int) -> None:
    for i in range(height):
        idx = i
        y = top + i
        if idx >= len(state.menu):
            safe_addstr(stdscr, y, 0, " " * left_w, state.theme.normal)
            continue
        label = state.menu[idx]
        selected = idx == state.selected
        prefix = "> " if selected else "  "
        text = (prefix + label)[:left_w].ljust(left_w)
        # When focus is not on the menu, keep the highlight but make it less "actionable".
        attr = state.theme.reverse if selected else state.theme.normal
        if selected and state.focus != "menu":
            attr = state.theme.normal | curses.A_BOLD
        safe_addstr(stdscr, y, 0, text, attr)


def _draw_viewer(stdscr, state: AppState, top: int, cols: int, rows: int) -> None:
    assert state.viewer is not None
    v = state.viewer
    body_h = rows - top - 1
    title = f"[Viewer] {v.title}"
    safe_addstr(stdscr, top, 0, title[:cols].ljust(cols), state.theme.normal)
    for i in range(body_h - 1):
        src_idx = v.top + i
        y = top + 1 + i
        if src_idx >= len(v.lines):
            safe_addstr(stdscr, y, 0, " " * cols, state.theme.normal)
            continue
        safe_addstr(stdscr, y, 0, v.lines[src_idx][:cols].ljust(cols), state.theme.normal)


def _draw_right_pane(stdscr, state: AppState, top: int, left_w: int, cols: int, rows: int) -> None:
    body_h = rows - top - 1
    right_x = left_w + 1
    right_w = max(0, cols - right_x)

    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    preview: List[str] = []
    if label == "Entropy Sources":
        preview = _entropy_sources_preview(state, width=right_w, height=body_h)
    elif label == "Drop Zone":
        preview = _dropzone_preview(state, width=right_w, height=body_h)
    elif label == "Stamp Pack":
        preview = [
            "Stamp a content-addressed EntropyPack from a directory.",
            "",
            "Writes:",
            "- manifest.json",
            "- entropy_root_sha256.txt",
            "- receipt.json",
            "- payload/...",
            "- optional entropy_pack.zip",
            "",
            "Seed policy:",
            "- derive seed -> fingerprint in receipt",
            "- write seed files only if requested",
        ]
    elif label == "Verify Pack":
        preview = [
            "Verify a stamped pack (dir or .zip).",
            "",
            "Checks:",
            "- manifest root hash",
            "- file sizes + sha256",
        ]
    elif label.startswith("View "):
        preview = ["Open a read-only viewer for pinned docs."]
    elif label == "Quit":
        preview = ["Exit EPS."]

    # If we have log output, show it instead of the generic preview.
    if state.log_lines and label not in ("Drop Zone", "Entropy Sources"):
        preview = state.log_lines[-(body_h - 1) :]

    for i in range(body_h):
        y = top + i
        if i >= len(preview):
            line = ""
        else:
            line = preview[i]
        safe_addstr(stdscr, y, left_w, "|", state.theme.normal)
        safe_addstr(stdscr, y, right_x, line[:right_w].ljust(right_w), state.theme.normal)


def draw(stdscr, state: AppState) -> None:
    rows, cols = stdscr.getmaxyx()
    stdscr.erase()

    if state.insane and state.palette is not None:
        _draw_insane_background(stdscr, state, rows, cols)
        _draw_insane_header(stdscr, state, cols)
    else:
        _draw_header(stdscr, state, cols)
    body_top = 3

    if state.viewer is not None:
        if state.insane and state.palette is not None:
            _draw_insane_viewer(stdscr, state, body_top, cols, rows)
            _draw_footer(stdscr, state, rows, cols)
        else:
            _draw_viewer(stdscr, state, body_top, cols, rows)
            _draw_footer(stdscr, state, rows, cols)
        stdscr.refresh()
        return

    # Menu on the left, fixed width.
    left_w = min(28, max(18, cols // 3))
    body_h = rows - body_top - 1

    if state.insane and state.palette is not None:
        _draw_insane_menu(stdscr, state, body_top, left_w, body_h)
        _draw_insane_right_pane(stdscr, state, body_top, left_w, cols, rows)
        _draw_footer(stdscr, state, rows, cols)
    else:
        _draw_menu(stdscr, state, body_top, left_w, body_h)
        _draw_right_pane(stdscr, state, body_top, left_w, cols, rows)
        _draw_footer(stdscr, state, rows, cols)
    stdscr.refresh()


def _run_outside_curses(stdscr, fn: Callable[[], None]) -> None:
    try:
        curses.def_prog_mode()
    except curses.error:
        pass
    try:
        curses.endwin()
    except curses.error:
        pass

    try:
        fn()
    finally:
        try:
            input("\n(EPS) Press Enter to return... ")
        except EOFError:
            pass
        try:
            curses.reset_prog_mode()
        except curses.error:
            pass
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            stdscr.keypad(True)
            stdscr.nodelay(False)
            stdscr.timeout(100)
        except curses.error:
            pass


def _prompt_str_curses(stdscr, label: str, *, default: str = "", max_len: int = 512) -> str:
    rows, cols = stdscr.getmaxyx()
    prompt = f"{label} [{default}]: " if default else f"{label}: "
    y = rows - 1
    stdscr.move(y, 0)
    stdscr.clrtoeol()
    safe_addstr(stdscr, y, 0, prompt[:cols], curses.A_REVERSE)
    stdscr.refresh()
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    curses.echo()
    try:
        # Allow long paste (e.g. drag-dropped absolute paths). ncurses will scroll horizontally;
        # constraining to screen width truncates paths and makes drop feel "broken".
        raw = stdscr.getstr(y, min(len(prompt), max(0, cols - 1)), int(max_len))
    finally:
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
    s = ""
    try:
        s = raw.decode("utf-8", errors="ignore").strip()
    except Exception:
        s = str(raw).strip()
    return s if s else str(default)


def _prompt_bool_curses(stdscr, label: str, *, default: bool = False) -> bool:
    d = "y" if default else "n"
    s = _prompt_str_curses(stdscr, f"{label} (y/n)", default=d, max_len=5).strip().lower()
    if not s:
        return bool(default)
    return s.startswith("y") or s in ("1", "true", "yes")


def _identify_image_dims(path: Path) -> Optional[str]:
    magick = shutil.which("magick")
    if not magick:
        return None
    try:
        proc = subprocess.run(
            [magick, "identify", "-format", "%w x %h", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        out = (proc.stdout or "").strip()
        return out if out else None
    except Exception:
        return None


def _action_entropy_add_photos(state: AppState, stdscr) -> None:
    p_s = _prompt_str_curses(stdscr, "(EPS) photo path (file or dir)", default=".")
    p = Path(p_s).expanduser()
    if not p.exists():
        state.status = "Failed."
        state.log_lines = [f"Entropy add failed: not found: {p}"]
        return
    paths: List[Path] = []
    if p.is_file():
        paths = [p]
    elif p.is_dir():
        # Deterministic scan. Bound it to keep the UI responsive.
        for fp in sorted(p.rglob("*")):
            try:
                if fp.is_file() and _is_image_path(fp):
                    paths.append(fp)
            except OSError:
                continue
            if len(paths) >= 250:
                break
    else:
        state.status = "Failed."
        state.log_lines = [f"Entropy add failed: not a file/dir: {p}"]
        return

    added = 0
    for fp in paths:
        try:
            sha, size = _sha256_hex_path(fp, max_bytes=100 * 1024 * 1024)
        except Exception:
            continue
        name = fp.name
        dims = _identify_image_dims(fp)
        meta: Dict[str, object] = {}
        if dims:
            meta["dims"] = dims
        state.entropy_sources.append(
            EntropySource(kind="photo", name=name, sha256=sha, size_bytes=size, meta=meta, path=fp)
        )
        added += 1

    if added:
        state.status = "Done."
        state.log_lines = [f"Added {added} photo source(s)."]
    else:
        state.status = "Failed."
        state.log_lines = ["No valid images found or hash failed."]


def _action_entropy_add_text(state: AppState, stdscr) -> None:
    label = _prompt_str_curses(stdscr, "(EPS) text label", default="note")
    text = _prompt_str_curses(stdscr, "(EPS) text (one line)", default="", max_len=4096)
    raw = text.encode("utf-8", errors="ignore")
    sha = hashlib.sha256(raw).hexdigest()
    state.entropy_sources.append(
        EntropySource(kind="text", name=label.strip() or "note", sha256=sha, size_bytes=len(raw), text=text)
    )
    state.status = "Done."
    state.log_lines = [f"Added text source: {label.strip() or 'note'} ({_fmt_bytes(len(raw))})."]


def _action_entropy_tap(state: AppState, stdscr) -> None:
    """
    Capture keystroke timing + codes for entropy.
    ESC ends early.
    """
    target = 256
    h = hashlib.sha256()
    sample_events: List[Tuple[int, int]] = []
    count = 0
    start_ns = time.monotonic_ns()
    last_ns = start_ns

    # Temporarily switch to non-blocking reads for this capture.
    try:
        stdscr.nodelay(True)
        stdscr.timeout(25)
    except curses.error:
        pass

    try:
        while True:
            now = time.monotonic_ns()
            dt_us = int((now - last_ns) // 1000)
            last_ns = now
            try:
                ch = stdscr.getch()
            except curses.error:
                ch = -1
            if ch == -1:
                # Update status without adding entropy.
                state.status = f"Tap entropy: {count}/{target} ..."
                draw(stdscr, state)
                continue
            if ch == 27:  # ESC
                break
            code = int(ch) & 0xFFFFFFFF
            # Mix timing + code deterministically into the hash.
            h.update(dt_us.to_bytes(4, "little", signed=False))
            h.update(code.to_bytes(4, "little", signed=False))
            count += 1
            if len(sample_events) < 64:
                sample_events.append((dt_us, code))
            if count >= target:
                break
            state.status = f"Tap entropy: {count}/{target} ..."
            draw(stdscr, state)
    finally:
        try:
            stdscr.nodelay(False)
            stdscr.timeout(50 if state.insane else 100)
        except curses.error:
            pass

    digest = h.hexdigest()
    meta: Dict[str, object] = {"events": int(count), "sample": sample_events[:16]}
    # size_bytes is the conceptual size of the captured stream.
    state.entropy_sources.append(EntropySource(kind="tap", name="tap", sha256=digest, size_bytes=target * 8, meta=meta))
    # Reward hook: stub out where we will play a sound effect later.
    state.reward_ticks = 18
    state.status = "Entropy collected."
    state.log_lines = [
        f"Entropy collected: tap events={count}",
        f"tap_sha256: {digest}",
        "(SFX stub) jackpot",
    ]


def _action_entropy_delete_selected(state: AppState) -> None:
    if not state.entropy_sources:
        return
    idx = max(0, min(int(state.entropy_selected), len(state.entropy_sources) - 1))
    removed = state.entropy_sources.pop(idx)
    state.entropy_selected = max(0, min(state.entropy_selected, len(state.entropy_sources) - 1))
    state.status = "Done."
    state.log_lines = [f"Removed source: [{removed.kind}] {removed.name}."]


def _action_entropy_clear(state: AppState) -> None:
    n = len(state.entropy_sources)
    state.entropy_sources.clear()
    state.entropy_selected = 0
    state.status = "Done."
    state.log_lines = [f"Cleared {n} source(s)."]


def _action_entropy_preview(state: AppState) -> None:
    if not state.entropy_sources:
        return
    idx = max(0, min(int(state.entropy_selected), len(state.entropy_sources) - 1))
    s = state.entropy_sources[idx]
    lines: List[str] = []
    lines.append(f"[{s.kind}] {s.name}")
    lines.append(f"sha256: {s.sha256}")
    lines.append(f"size: {_fmt_bytes(s.size_bytes)}")
    if s.meta:
        lines.append("")
        lines.append("meta:")
        for k in sorted(s.meta.keys()):
            lines.append(f"- {k}: {s.meta[k]}")
    if s.kind == "text" and s.text is not None:
        lines.append("")
        lines.append("text:")
        lines.extend((s.text or "").splitlines() or [""])
    if s.kind == "photo" and s.path is not None and s.path.is_file():
        lines.append("")
        lines.append("preview:")
        lines.extend(_image_ascii_cached(s.path, cols=72, rows=28))
    open_viewer(state, f"Entropy Source: {s.kind}", lines)


def _prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{label} [{suffix}]: ").strip().lower()
    if not raw:
        return bool(default)
    return raw in ("y", "yes", "true", "1")


def _build_sources_payload_dir(sources: Sequence[EntropySource]) -> Path:
    """
    Materialize staged entropy sources into a real directory so they can be stamped as artifacts.
    Photos are copied; text/tap become files. The caller owns cleanup.
    """
    td = Path(tempfile.mkdtemp(prefix="eps_payload_sources_"))
    (td / "photos").mkdir(parents=True, exist_ok=True)
    (td / "text").mkdir(parents=True, exist_ok=True)
    (td / "tap").mkdir(parents=True, exist_ok=True)

    index: List[Dict[str, object]] = []
    for i, s in enumerate(sources, start=1):
        entry: Dict[str, object] = {
            "i": int(i),
            "kind": s.kind,
            "name": s.name,
            "sha256": s.sha256,
            "size_bytes": int(s.size_bytes),
            "meta": dict(s.meta or {}),
        }
        if s.kind == "photo" and s.path is not None and s.path.is_file():
            dst = td / "photos" / f"{i:03d}_{s.path.name}"
            try:
                shutil.copy2(s.path, dst)
            except Exception:
                pass
            entry["path"] = str(Path("photos") / dst.name)
        elif s.kind == "text" and s.text is not None:
            dst = td / "text" / f"{i:03d}_{re.sub(r'[^A-Za-z0-9._-]+', '_', s.name)[:40] or 'note'}.txt"
            dst.write_text(s.text, encoding="utf-8", errors="ignore")
            try:
                dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            entry["path"] = str(Path("text") / dst.name)
        elif s.kind == "tap":
            dst = td / "tap" / f"{i:03d}_tap.json"
            dst.write_text(json.dumps(entry, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            try:
                dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            entry["path"] = str(Path("tap") / dst.name)
        index.append(entry)

    (td / "sources.json").write_text(json.dumps(index, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return td


def _write_entropy_sources_into_pack(pack_dir: Path, sources: Sequence[EntropySource]) -> Optional[Path]:
    """
    Persist staged sources into the pack directory (outside payload/) for audit.
    These files are excluded from entropy_pack.zip.
    """
    out = pack_dir / "entropy_sources"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    index: List[Dict[str, object]] = []
    for i, s in enumerate(sources, start=1):
        entry: Dict[str, object] = {
            "i": int(i),
            "kind": s.kind,
            "name": s.name,
            "sha256": s.sha256,
            "size_bytes": int(s.size_bytes),
            "meta": dict(s.meta or {}),
        }
        if s.kind == "photo" and s.path is not None and s.path.is_file():
            dst = out / f"{i:03d}_{s.path.name}"
            try:
                shutil.copy2(s.path, dst)
            except Exception:
                pass
            entry["path"] = dst.name
        elif s.kind == "text" and s.text is not None:
            dst = out / f"{i:03d}_{re.sub(r'[^A-Za-z0-9._-]+', '_', s.name)[:40] or 'note'}.txt"
            dst.write_text(s.text, encoding="utf-8", errors="ignore")
            try:
                dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            entry["path"] = dst.name
        elif s.kind == "tap":
            dst = out / f"{i:03d}_tap.json"
            dst.write_text(json.dumps(entry, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            try:
                dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            entry["path"] = dst.name
        index.append(entry)
    (out / "sources.index.json").write_text(json.dumps(index, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return out


def _action_stamp(state: AppState, stdscr) -> None:
    # In-curses prompt sequence (no dropping out of the UI).
    if state.insane and not state.entropy_sources:
        _fx_no_entropy(stdscr, state, duration_s=5.0, fps=25)
        state.status = "Failed."
        state.log_lines = ["Stamp blocked: no entropy sources staged.", "Go to Entropy Sources or Drop Zone and add some first."]
        state.selected = 0  # Entropy Sources
        state.focus = "entropy"
        return

    state.status = "Stamp: configure..."
    state.log_lines = []
    rows, cols = stdscr.getmaxyx()
    stdscr.move(rows - 1, 0)
    stdscr.clrtoeol()
    stdscr.refresh()

    default_out = str(state.last_out_dir) if state.last_out_dir is not None else "./out"
    default_in = str(state.last_input_dir) if state.last_input_dir is not None else "."
    input_s = _prompt_str_curses(stdscr, "(EPS) input dir (or @sources)", default=default_in)
    out_s = _prompt_str_curses(stdscr, "(EPS) out dir", default=default_out)
    pack_id_s = _prompt_str_curses(stdscr, "(EPS) pack_id (optional)", default="")
    notes_s = _prompt_str_curses(stdscr, "(EPS) notes (optional)", default="")
    created_at_s = _prompt_str_curses(stdscr, "(EPS) created_at_utc (optional)", default="")
    include_hidden = _prompt_bool_curses(stdscr, "(EPS) include hidden files", default=False)
    exclude_picker = False
    if input_s.strip() != "@sources":
        exclude_picker = _prompt_bool_curses(stdscr, "(EPS) exclude artifacts before stamping (picker)", default=False)
    zip_pack = _prompt_bool_curses(stdscr, "(EPS) write entropy_pack.zip", default=True)
    derive_seed = _prompt_bool_curses(stdscr, "(EPS) derive seed_master (LOCKDOWN)", default=bool(state.insane))
    mix_sources = False
    pool_sha = None
    if derive_seed and state.entropy_sources:
        mix_sources = _prompt_bool_curses(stdscr, "(EPS) mix staged entropy sources into seed", default=True)
        if mix_sources:
            if len(state.entropy_sources) < int(state.entropy_min_sources):
                need_more = int(state.entropy_min_sources) - len(state.entropy_sources)
                state.status = "Failed."
                state.log_lines = [
                    "Stamp blocked: not enough entropy sources.",
                    f"Need {state.entropy_min_sources}, have {len(state.entropy_sources)}.",
                    f"Add {need_more} more (photos/text/tap), then try again.",
                ]
                # Guide the user back to the deterministic path: go stage more sources now.
                state.selected = 0  # Entropy Sources
                state.focus = "entropy"
                return
            pool_sha = _entropy_pool_sha256(state.entropy_sources)
    write_seed = _prompt_bool_curses(stdscr, "(EPS) write seed files (chmod 600)", default=False) if derive_seed else False
    show_seed = _prompt_bool_curses(stdscr, "(EPS) show seed in UI", default=False) if derive_seed else False
    write_sources_default = bool(mix_sources)  # if sources affect the seed, default to auditing them
    write_sources = (
        _prompt_bool_curses(
            stdscr,
            "(EPS) write entropy_sources into pack (excluded from entropy_pack.zip)",
            default=write_sources_default,
        )
        if derive_seed
        else False
    )
    evidence_default = bool(derive_seed)
    evidence_bundle = _prompt_bool_curses(stdscr, "(EPS) write evidence bundle zip (tamper-evident)", default=evidence_default)

    tmp_payload_dir: Optional[Path] = None
    input_dir: Path
    exclude_relpaths: Optional[Set[str]] = None
    if input_s.strip() == "@sources":
        if not state.entropy_sources:
            state.status = "Failed."
            state.log_lines = ["@sources selected, but no entropy sources are staged."]
            return
        tmp_payload_dir = _build_sources_payload_dir(state.entropy_sources)
        input_dir = tmp_payload_dir
    else:
        input_dir = Path(input_s).expanduser()
        if exclude_picker:
            picked = _artifact_exclude_picker(stdscr, state, input_dir=input_dir, include_hidden=bool(include_hidden))
            if picked is None:
                state.status = "Ready."
                state.log_lines = ["Artifact exclude picker cancelled."]
                return
            exclude_relpaths = set(picked)
    out_dir = Path(out_s).expanduser()
    pack_id = pack_id_s.strip() or None
    notes = notes_s.strip() or None
    created_at = created_at_s.strip() or None

    def _do_stamp() -> StampResult:
        return stamp_pack(
            input_dir=input_dir,
            out_dir=out_dir,
            pack_id=pack_id,
            notes=notes,
            created_at_utc=created_at,
            include_hidden=include_hidden,
            exclude_relpaths=sorted(exclude_relpaths) if exclude_relpaths else None,
            zip_pack=zip_pack,
            derive_seed=derive_seed,
            entropy_sources_sha256=pool_sha if mix_sources else None,
            evidence_bundle=False,  # do this post-stamp so it can include entropy_sources if we choose to write them
            write_seed_files=write_seed,
            print_seed=False,  # never print to stdout from TUI
        )

    try:
        if state.insane:
            res = _stamp_with_insane_fx(stdscr, state, _do_stamp, min_stamping_s=5.0, created_s=5.0)
        else:
            res = _do_stamp()
    except Exception as exc:
        state.log_lines = ["Stamp failed.", f"- {exc}"]
        state.status = "Failed."
        if tmp_payload_dir is not None:
            try:
                shutil.rmtree(tmp_payload_dir)
            except Exception:
                pass
        return
    finally:
        if tmp_payload_dir is not None:
            try:
                shutil.rmtree(tmp_payload_dir)
            except Exception:
                pass

    state.last_pack_dir = res.pack_dir
    state.last_out_dir = out_dir.resolve()
    state.log_lines = [
        "Stamp complete.",
        f"input_dir: {input_dir.resolve()}",
        f"out_dir: {out_dir.resolve()}",
        f"pack_dir: {res.pack_dir}",
        f"entropy_root_sha256: {res.root_sha256}",
    ]
    if exclude_relpaths:
        state.log_lines.append(f"excluded_artifacts: {len(exclude_relpaths)}")
    fp = res.receipt.get("seed_fingerprint_sha256")
    if isinstance(fp, str) and fp:
        state.log_lines.append(f"seed_fingerprint_sha256: {fp}")
    if mix_sources and pool_sha:
        state.log_lines.append(f"entropy_sources_count: {len(state.entropy_sources)}")
        state.log_lines.append(f"entropy_sources_sha256: {pool_sha}")
    if write_sources and derive_seed and state.entropy_sources:
        p = _write_entropy_sources_into_pack(res.pack_dir, state.entropy_sources)
        if p is not None:
            state.log_lines.append(f"entropy_sources_dir: {p}")
    if evidence_bundle:
        try:
            ev_path, ev_sha = write_evidence_bundle(res.pack_dir)
            state.log_lines.append(f"evidence_bundle: {ev_path}")
            if ev_sha:
                state.log_lines.append(f"evidence_bundle_sha256: {ev_sha}")
            # Persist in receipt.json for agent consumption.
            res.receipt["evidence_bundle_path"] = str(Path(ev_path).name)
            if ev_sha:
                res.receipt["evidence_bundle_sha256"] = str(ev_sha)
            try:
                (res.pack_dir / "receipt.json").write_text(
                    json.dumps(res.receipt, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
        except Exception as exc:
            state.log_lines.append(f"evidence_bundle failed: {exc}")
    if show_seed and res.seed_master is not None:
        seed_hex = res.seed_master.hex()
        seed_b64 = base64.b64encode(res.seed_master).decode("ascii")
        state.log_lines.append(f"seed_master.hex: {seed_hex}")
        state.log_lines.append(f"seed_master.b64: {seed_b64}")
    state.status = "Done."


def _action_verify(state: AppState, stdscr) -> None:
    state.status = "Verify: configure..."
    state.log_lines = []
    default = str(state.last_pack_dir) if state.last_pack_dir is not None else (str(state.last_out_dir) if state.last_out_dir is not None else "./out")
    pack_s = _prompt_str_curses(stdscr, "(EPS) pack path (dir or .zip)", default=default)
    pack = Path(pack_s).expanduser()
    allow_large_manifest = _prompt_bool_curses(
        stdscr,
        "(EPS) allow large manifest.json (32 MiB cap)",
        default=bool(state.insane),
    )
    if pack.is_dir() and not (pack / "manifest.json").is_file():
        # If the user pointed at an out/ directory, pick the most recent pack subdir.
        try:
            candidates = [p for p in pack.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
        except Exception:
            candidates = []
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            picked = candidates[0]
            state.log_lines = [f"Auto-selected pack dir: {picked}"]
            pack = picked
    try:
        cap = (32 * 1024 * 1024) if allow_large_manifest else int(DEFAULT_MAX_MANIFEST_BYTES)
        res = verify_pack(pack, max_manifest_bytes=int(cap))
    except Exception as exc:
        state.log_lines = ["Verify failed.", f"- {exc}"]
        state.status = "Failed."
        return
    if res.ok:
        state.log_lines = [
            "Verify ok.",
            f"entropy_root_sha256: {res.root_sha256}",
            f"artifact_count_verified: {res.file_count}",
            f"artifact_bytes_verified: {res.total_bytes}",
        ]
        state.status = "Done."
    else:
        state.log_lines = ["Verify failed."] + [f"- {e}" for e in res.errors]
        if any("missing manifest.json" in e for e in res.errors):
            state.log_lines.append("")
            state.log_lines.append("Hint: verify expects a stamped pack dir (out/<root_sha256>/) or entropy_pack.zip.")
        if any("manifest.json" in e and "file too large" in e for e in res.errors):
            state.log_lines.append("")
            state.log_lines.append("Hint: manifest.json is a file-list; very large packs need a larger cap (enable 'allow large manifest').")
        state.status = "Failed."


def handle_key(stdscr, state: AppState, ch: int) -> bool:
    if state.viewer is not None:
        if ch in (27, ord("q"), ord("Q"), 10, 13, curses.KEY_ENTER, curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8, ord("b"), ord("B")):
            close_viewer(state)
            return True
        if ch in (curses.KEY_UP, ord("k")):
            state.viewer.top = max(0, state.viewer.top - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            state.viewer.top = min(max(0, len(state.viewer.lines) - 1), state.viewer.top + 1)
        elif ch == curses.KEY_PPAGE:
            state.viewer.top = max(0, state.viewer.top - 10)
        elif ch == curses.KEY_NPAGE:
            state.viewer.top = min(max(0, len(state.viewer.lines) - 1), state.viewer.top + 10)
        return True

    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    if label == "Drop Zone" and ch in (ord("q"), ord("Q"), curses.KEY_EXIT):
        # Prevent accidental quit-jumps from high-volume paste/drop sequences.
        return True

    # Back / quit semantics:
    # - Esc backs out of focus modes (and clears transient status), but does not hard-quit.
    # - q is a two-step quit: first press jumps to Quit; second press exits.
    if ch == 27:
        if state.focus != "menu":
            state.focus = "menu"
        state.status = "Ready."
        return True
    if ch in (ord("q"), ord("Q"), curses.KEY_EXIT):
        if label == "Quit":
            return False
        # Arm quit by selecting the Quit menu item.
        state.selected = len(state.menu) - 1
        state.focus = "menu"
        state.status = "Ready. (Enter to quit)"
        return True

    # Focus toggle.
    if ch in (9, getattr(curses, "KEY_BTAB", -999)):
        if label == "Entropy Sources":
            state.focus = "entropy" if state.focus == "menu" else "menu"
        else:
            state.focus = "menu"
        return True

    if label == "Drop Zone":
        # Capture paste/drag streams as data instead of letting them trigger random actions.
        if 32 <= ch <= 126:
            state.drop_paste_buf += chr(int(ch))
            state.drop_paste_last_ns = time.monotonic_ns()
            return True
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            state.drop_paste_buf = state.drop_paste_buf[:-1]
            state.drop_paste_last_ns = time.monotonic_ns()
            return True
        if ch in (curses.KEY_ENTER, 10, 13):
            # If a paste stream includes a newline, treat it as part of the payload (multiple paths)
            # instead of committing immediately.
            if state.drop_paste_buf and state.drop_paste_last_ns:
                now = time.monotonic_ns()
                if (now - int(state.drop_paste_last_ns)) < 75_000_000:  # 75ms
                    state.drop_paste_buf += "\n"
                    state.drop_paste_last_ns = now
                    return True
            # If we already have buffered paste, commit it immediately.
            if state.drop_paste_buf:
                paths = _split_drop_payload(state.drop_paste_buf)
                state.drop_paste_buf = ""
                state.drop_paste_last_ns = 0
                if paths:
                    msgs = _apply_drop_paths(state, paths, max_apply=7)
                    ok = _count_drop_success(msgs)
                    if ok > 0:
                        state.drop_import_count += ok
                        state.drop_last_msgs = msgs[:4]
                        state.drop_last_msgs_ticks = 40
                        state.drop_flash_ticks = 10
                    else:
                        state.drop_last_msgs = msgs[:4]
                        state.drop_last_msgs_ticks = 40
                return True
            # Provide a focused "landing zone" input. If the user drags a folder into the terminal,
            # many terminals will paste the path into this field.
            raw = _prompt_str_curses(stdscr, "(EPS) drop path(s)", default=str(state.last_input_dir or ""), max_len=4096)
            paths = _split_drop_payload(raw)
            if not paths:
                state.status = "Ready."
                return True
            msgs = _apply_drop_paths(state, paths, max_apply=7)
            ok = _count_drop_success(msgs)
            if ok > 0:
                state.drop_import_count += ok
                state.drop_last_msgs = msgs[:4]
                state.drop_last_msgs_ticks = 40
                state.drop_flash_ticks = 10
            else:
                state.drop_last_msgs = msgs[:4]
                state.drop_last_msgs_ticks = 40
            return True
        # Do not swallow other keys; Up/Down should still navigate the menu.

    # Entropy Sources mode has its own navigation/actions.
    if label == "Entropy Sources":
        if ch in (curses.KEY_UP, ord("k")) and state.focus == "entropy":
            state.entropy_selected = max(0, state.entropy_selected - 1)
            state.status = "Ready."
            return True
        if ch in (curses.KEY_DOWN, ord("j")) and state.focus == "entropy":
            state.entropy_selected = min(max(0, len(state.entropy_sources) - 1), state.entropy_selected + 1)
            state.status = "Ready."
            return True
        if ch in (ord("a"), ord("A")):
            _action_entropy_add_photos(state, stdscr)
            return True
        if ch in (ord("t"), ord("T")):
            _action_entropy_add_text(state, stdscr)
            return True
        if ch == ord(" "):
            _action_entropy_tap(state, stdscr)
            return True
        if ch in (ord("d"), ord("D")):
            _action_entropy_delete_selected(state)
            return True
        if ch in (ord("c"), ord("C")):
            _action_entropy_clear(state)
            return True
        if ch in (curses.KEY_ENTER, 10, 13) and state.focus == "entropy":
            _action_entropy_preview(state)
            return True

    if ch in (curses.KEY_UP, ord("k")):
        state.selected = max(0, state.selected - 1)
        label2 = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
        state.focus = "entropy" if label2 == "Entropy Sources" else "menu"
        state.status = "Ready."
        state.log_lines = []
        return True
    if ch in (curses.KEY_DOWN, ord("j")):
        state.selected = min(len(state.menu) - 1, state.selected + 1)
        label2 = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
        state.focus = "entropy" if label2 == "Entropy Sources" else "menu"
        state.status = "Ready."
        state.log_lines = []
        return True

    if ch in (curses.KEY_ENTER, 10, 13):
        if label == "Stamp Pack":
            _action_stamp(state, stdscr)
            return True
        if label == "Verify Pack":
            _action_verify(state, stdscr)
            return True
        if label == "Entropy Sources":
            # With focus on the menu, Enter toggles focus into the entropy list.
            if state.focus == "menu":
                state.focus = "entropy"
            return True
        if label == "Drop Zone":
            return True
        if label == "View README":
            root = Path(__file__).resolve().parents[1]
            open_viewer(state, "README.md", _read_text_lines(root / "README.md"))
            return True
        if label == "View TUI Standard":
            root = Path(__file__).resolve().parents[1]
            open_viewer(state, "TUI_STANDARD_v0.1.0.md", _read_text_lines(root / "ssot" / "ui" / "TUI_STANDARD_v0.1.0.md"))
            return True
        if label == "View TUI Contract":
            root = Path(__file__).resolve().parents[1]
            open_viewer(state, "TUI_CONTRACT_v0.0.4.md", _read_text_lines(root / "ssot" / "ui" / "TUI_CONTRACT_v0.0.4.md"))
            return True
        if label == "Quit":
            return False
        return True

    if ch == curses.KEY_RESIZE:
        return True
    return True


def run_tui(stdscr, *, insane: bool = False, godel_source: Optional[str] = None) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    try:
        curses.set_escdelay(25)
    except curses.error:
        pass

    theme = init_theme()
    pal = init_insane_palette() if insane else None
    state = AppState(theme=theme, insane=bool(insane), palette=pal, tick=0)
    if insane:
        # Default: use bundled Gödel word bank from repo assets (no runtime PDF/OCR dependency).
        words: List[str] = _load_bundled_godel_words()
        src_s = (godel_source or "").strip()
        if src_s:
            src = _resolve_godel_source(src_s)
            if src is None:
                state.godel_phrase = "NO GODEL SOURCE"
            else:
                if src.suffix.lower() == ".pdf":
                    # Intentionally skip PDF runtime extraction; keep bundled words.
                    if not words:
                        state.godel_phrase = "PDF SOURCE DISABLED"
                else:
                    src_words = _load_wordlist_from_source(src, max_bytes=5_000_000)
                    if src_words:
                        words = src_words
                    elif not words:
                        state.godel_phrase = "EMPTY GODEL TEXT"
        state.godel_words = words
        if words:
            _update_godel_phrase(state, min_interval_ticks=0)
        elif not state.godel_phrase:
            state.godel_phrase = "EMPTY GODEL WORDS"

    stdscr.keypad(True)
    stdscr.nodelay(False)
    stdscr.timeout(50 if insane else 100)

    while True:
        state.tick += 1
        if state.reward_ticks > 0:
            state.reward_ticks -= 1
        if state.drop_flash_ticks > 0:
            state.drop_flash_ticks -= 1
        if state.drop_last_msgs_ticks > 0:
            state.drop_last_msgs_ticks -= 1
            if state.drop_last_msgs_ticks == 0:
                state.drop_last_msgs = []
        # If user drag-dropped a path into the terminal while on Drop Zone, it likely arrived as a fast paste
        # stream. Auto-commit after a short idle gap.
        if state.drop_paste_buf and state.drop_paste_last_ns:
            if (time.monotonic_ns() - int(state.drop_paste_last_ns)) > 300_000_000:  # 300ms
                paths = _split_drop_payload(state.drop_paste_buf)
                state.drop_paste_buf = ""
                state.drop_paste_last_ns = 0
                if paths:
                    msgs = _apply_drop_paths(state, paths, max_apply=7)
                    ok = _count_drop_success(msgs)
                    if ok > 0:
                        state.drop_import_count += ok
                        state.drop_last_msgs = msgs[:4]
                        state.drop_last_msgs_ticks = 40
                        state.drop_flash_ticks = 10
                    else:
                        state.drop_last_msgs = msgs[:4]
                        state.drop_last_msgs_ticks = 40
        # Poll the filesystem landing zone occasionally (not every tick) to stay cheap.
        if state.tick % 4 == 0:
            _poll_drop_dir(state)
        draw(stdscr, state)
        try:
            ch = stdscr.getch()
        except curses.error:
            ch = -1
        if ch == -1:
            continue
        keep_running = handle_key(stdscr, state, ch)
        if not keep_running:
            break


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="eps-tui")
    p.add_argument("--insane", action="store_true", help="Enable non-conforming neon TUI skin")
    p.add_argument(
        "--godel-source",
        default=None,
        help="Optional text/markdown path for header words (insane mode). PDFs are ignored; bundled words are used.",
    )
    ns = p.parse_args(list(argv) if argv is not None else None)

    # The curses TUI must run in a real terminal/pty. When launched from an IDE
    # or a non-interactive environment, curses will crash with setupterm errors.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("eps-tui: error: no TTY detected (curses requires an interactive terminal).", file=sys.stderr)
        print(
            "hint: run from Terminal.app/iTerm2, or use the headless CLI: "
            "`python3 -m eps stamp --input <dir> --out ./out`",
            file=sys.stderr,
        )
        return 2

    try:
        curses.wrapper(lambda stdscr: run_tui(stdscr, insane=bool(ns.insane), godel_source=ns.godel_source))
    except curses.error as exc:
        # Keep failure mode readable (no traceback) for common terminal issues.
        msg = str(exc).strip() or exc.__class__.__name__
        print(f"eps-tui: error: {msg}", file=sys.stderr)
        if "setupterm" in msg:
            print("hint: ensure `TERM` is set (e.g. xterm-256color) and run inside a real terminal.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
