#!/usr/bin/env python3
"""
Minimal curses TUI for Authored Pack.

Profiles:
- Calm (default): workflow-first, quiet, and readable for busy or nervous humans.
- Noisy (optional): louder ceremony cues and motion with the same pack/seed outputs.

Baseline posture:
- 3-line header band (identity + status + divider)
- left menu + right preview/log
- action execution runs outside curses (prompt -> run -> return)

ASCII-first: no box-drawing or emoji in calm mode. Color is optional.

To start directly in the louder profile, run:
- `python3 -B bin/authored_pack.py --noisy`
"""

from __future__ import annotations

import argparse
import base64
import curses
import hashlib
import json
import math
import os
import queue
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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

# When running as `python3 bin/authored_pack.py`, Python prepends `bin/` to sys.path,
# which would cause `import authored_pack` to resolve to this file (bin/authored_pack.py).
# Force repo-root precedence so the package import resolves to `/authored_pack`.
_BIN_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BIN_DIR.parent
try:
    sys.path.remove(str(_BIN_DIR))
except ValueError:
    pass
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from authored_pack import __version__
from authored_pack.pack import DEFAULT_MAX_MANIFEST_BYTES, stamp_pack, verify_pack


APP_NAME = "AUTHORED PACK"
APP_VERSION = f"v{__version__}"
EPS_TUI_TITLE = "Main TUI"
EPS_TUI_VERSION = APP_VERSION
BUNDLED_GODEL_WORDS = _REPO_ROOT / "assets" / "godel_words.txt"

DIVIDER_WIDE = "-------+-------+-------+-------+-------+-------+-------+-------+-------+-------+"
DIVIDER_NARROW = "-------+-------+-------+-------+-------+-------+-------+----"

_UI_SFX_LOCK = threading.Lock()
_UI_SFX_CACHE: Dict[str, Path] = {}
_UI_SFX_LAST_NS: Dict[str, int] = {}
LOCKDOWN_MIN_TAP_EVENTS = 16
DEFAULT_ENTROPY_MIN_SOURCES = 7


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


def build_header_identity_line(
    app_name: str,
    tui_title: str,
    tui_version: str,
    width: int,
    context_suffix: str = "",
) -> str:
    w = max(0, int(width or 0))
    if w <= 0:
        return ""
    ctx = f" :: {context_suffix}" if context_suffix.strip() else ""
    left = f" {app_name} :: {tui_title}{ctx}"
    version = f" {tui_version} "
    if len(version) >= w:
        return version[-w:]
    left_w = max(0, w - len(version))
    return left[:left_w].ljust(left_w) + version


def _is_hidden_rel(rel_posix: str) -> bool:
    parts = str(rel_posix).split("/")
    return any(p.startswith(".") and p not in (".", "..") for p in parts)


def _scan_artifacts_for_picker(input_dir: Path, *, include_hidden: bool) -> List[Tuple[str, int]]:
    """
    Deterministic file scan (path + size) without hashing, for interactive exclude selection.
    Matches `authored_pack.manifest._iter_files` traversal order.
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

    stdscr.erase()
    attr_t = state.palette.header[0] if (state.insane and state.palette) else state.theme.header
    attr = state.palette.text if (state.insane and state.palette) else state.theme.normal
    safe_addstr(stdscr, 0, 0, "ARTIFACTS // EXCLUDE FROM NEXT STAMP".ljust(cols), attr_t)
    safe_addstr(stdscr, 2, 0, f"Scanning {_display_path(input_dir, max_len=max(24, cols - 12))} ...".ljust(cols), attr)
    stdscr.refresh()

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
        sub = f"input: {_display_path(Path(input_dir).resolve(), max_len=max(24, cols - 8))}"
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
            q2 = _prompt_str_curses(stdscr, "(Authored Pack) filter substring", default=query, max_len=200)
            if q2 is None:
                continue
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
class AuthoredSource:
    # Material is not stored by default for photos; we store file path + hash.
    kind: str  # "photo" | "text" | "tap"
    name: str
    sha256: str
    size_bytes: int
    meta: Dict[str, object] = field(default_factory=dict)
    path: Optional[Path] = None
    text: Optional[str] = None


@dataclass
class DropPreparedAction:
    message: str
    success: bool = False
    terminal: bool = False
    seen_key: Optional[str] = None
    input_dir: Optional[Path] = None
    source: Optional[AuthoredSource] = None


@dataclass
class DropBatchRequest:
    paths: List[str]
    seen_keys: List[Optional[str]]
    play_sfx: bool
    apply_mode: str = "auto"  # "auto" | "folder" | "sources"
    max_apply: Optional[int] = None


@dataclass
class StampConfig:
    input_mode: str = "folder"  # "folder" | "sources"
    input_path: str = ""
    out_path: str = "./out"
    pack_id: str = ""
    notes: str = ""
    created_at_utc: str = ""
    include_hidden: bool = False
    exclude_picker: bool = False
    zip_pack: bool = True
    derive_seed: bool = False
    mix_sources: bool = False
    write_seed: bool = False
    show_seed: bool = False
    write_sources: bool = False
    evidence_bundle: bool = False


@dataclass
class StampPanelRow:
    key: str
    label: str
    value: str = ""
    kind: str = "field"  # "field" | "toggle" | "action"


@dataclass
class VerifyConfig:
    pack_path: str = ""
    allow_large_manifest: bool = False


@dataclass
class AppState:
    theme: Theme
    insane: bool = False
    palette: Optional[InsanePalette] = None
    tick: int = 0
    godel_source_arg: Optional[str] = None
    godel_words: List[str] = field(default_factory=list)
    godel_phrase: str = ""
    godel_last_tick: int = 0
    authored_sources: List[AuthoredSource] = field(default_factory=list)
    entropy_selected: int = 0
    entropy_min_sources: int = DEFAULT_ENTROPY_MIN_SOURCES
    last_pack_dir: Optional[Path] = None
    last_out_dir: Optional[Path] = None
    last_input_dir: Optional[Path] = None
    # Use a stable, user-visible folder by default (inside the repo).
    drop_dir: Path = field(default_factory=lambda: _REPO_ROOT / "eps_drop")
    drop_seen: set[str] = field(default_factory=set)
    drop_last_count: int = 0
    drop_last_names: List[str] = field(default_factory=list)
    drop_flash_ticks: int = 0
    interaction_flash_ticks: int = 0
    drop_paste_buf: str = ""
    drop_paste_last_ns: int = 0
    drop_import_count: int = 0
    drop_last_msgs: List[str] = field(default_factory=list)
    drop_last_msgs_ticks: int = 0
    drop_pending_requests: List[DropBatchRequest] = field(default_factory=list)
    drop_results: "queue.SimpleQueue[Tuple[List[DropPreparedAction], bool]]" = field(default_factory=queue.SimpleQueue)
    drop_worker_busy: bool = False
    focus: str = "menu"  # "menu" | "entropy"
    current_lane: str = "folder"  # "folder" | "authored"
    reward_ticks: int = 0
    menu: List[str] = field(
        default_factory=lambda: [
            "Start",
            "Sources",
            "Stamp",
            "Verify",
            "Help",
        ]
    )
    selected: int = 0
    status: str = "Ready."
    log_lines: List[str] = field(default_factory=list)
    viewer: Optional[ViewerState] = None
    stamp_config: StampConfig = field(default_factory=StampConfig)
    stamp_panel_draft: Optional[StampConfig] = None
    stamp_panel_selected: int = 0
    stamp_panel_show_advanced: bool = False
    verify_config: VerifyConfig = field(default_factory=VerifyConfig)


def _set_current_lane(state: AppState, lane: str) -> None:
    state.current_lane = "authored" if str(lane).strip().lower() == "authored" else "folder"


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


def _shorten_middle(s: str, max_len: int) -> str:
    v = str(s or "")
    lim = max(8, int(max_len))
    if len(v) <= lim:
        return v
    keep = max(2, (lim - 3) // 2)
    tail = max(2, lim - 3 - keep)
    return f"{v[:keep]}...{v[-tail:]}"


def _display_path(path: Path | str | None, *, max_len: int = 40) -> str:
    if path is None:
        return "-"
    raw = str(path)
    try:
        p = Path(path)
        cwd = Path.cwd().resolve()
        home = Path.home().resolve()
        pp = p.expanduser()
        if pp.is_absolute():
            try:
                raw = pp.relative_to(cwd).as_posix()
                raw = f"./{raw}" if raw else "."
            except Exception:
                try:
                    raw = f"~/{pp.relative_to(home).as_posix()}"
                except Exception:
                    raw = pp.as_posix()
        else:
            raw = pp.as_posix()
    except Exception:
        raw = str(path)
    return _shorten_middle(raw, max_len=max_len)


def _iter_image_files_deterministic(root: Path, *, limit: int) -> List[Path]:
    """
    Deterministic bounded walk for interactive photo staging.
    Stops as soon as `limit` image files are found.
    """
    out: List[Path] = []
    stack: List[Path] = [Path(root)]
    while stack and len(out) < int(limit):
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                entries = sorted(list(it), key=lambda e: e.name)
        except OSError:
            continue
        dirs: List[Path] = []
        for ent in entries:
            try:
                if ent.is_dir(follow_symlinks=False):
                    dirs.append(Path(ent.path))
                    continue
                if ent.is_file(follow_symlinks=False) and _is_image_path(Path(ent.path)):
                    out.append(Path(ent.path))
                    if len(out) >= int(limit):
                        break
            except OSError:
                continue
        for d in reversed(dirs):
            stack.append(d)
    return out


def _sample_photo_import_paths(paths: Sequence[Path], *, target_count: int) -> List[Path]:
    sample_n = max(1, int(target_count))
    pool = list(paths)
    if len(pool) <= sample_n:
        return pool
    rng = random.SystemRandom()
    chosen = rng.sample(pool, sample_n)
    return sorted(chosen, key=lambda p: p.as_posix())


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


def _entropy_pool_sha256(sources: Sequence["AuthoredSource"]) -> str:
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


def _entropy_source_identity(s: "AuthoredSource") -> str:
    return f"{s.kind}:{s.sha256}:{int(s.size_bytes)}"


def _is_hex_sha256(s: str) -> bool:
    v = str(s or "")
    return len(v) == 64 and all(c in "0123456789abcdef" for c in v.lower())


def _entropy_source_is_lockdown_eligible(s: "AuthoredSource") -> bool:
    if not _is_hex_sha256(s.sha256):
        return False
    if int(s.size_bytes) <= 0:
        return False
    if s.kind == "tap":
        events = s.meta.get("events")
        if not isinstance(events, int):
            return False
        return int(events) >= int(LOCKDOWN_MIN_TAP_EVENTS)
    return True


def _lockdown_eligible_sources(sources: Sequence["AuthoredSource"]) -> List["AuthoredSource"]:
    out: List[AuthoredSource] = []
    seen: set[str] = set()
    for s in sources:
        if not _entropy_source_is_lockdown_eligible(s):
            continue
        sid = _entropy_source_identity(s)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(s)
    return out


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
    Many terminals paste dropped paths either quoted or with backslash-escapes.
    Normalize the common forms.
    """
    v = (s or "").strip()
    if not v:
        return ""
    if v.startswith("file://"):
        # Some desktop file managers paste file:// URLs in some contexts.
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


def _recover_existing_path_suffix(s: str) -> str:
    """
    Some terminal drag/drop flows can paste a duplicated absolute-path prefix.
    If the cleaned path does not exist, recover the last existing absolute-looking suffix.
    """
    v = (s or "").strip()
    if not v or v == "@sources":
        return v
    try:
        if Path(v).expanduser().exists():
            return v
    except OSError:
        return v

    positions: set[int] = set()
    for marker in ("/Users/", "/Volumes/", "/private/", "/tmp/", "/var/", "~/"):
        start = 1 if marker.startswith("/") else 0
        pos = v.find(marker, start)
        while pos != -1:
            positions.add(pos)
            pos = v.find(marker, pos + 1)

    for pos in sorted(positions, reverse=True):
        candidate = v[pos:]
        try:
            if Path(candidate).expanduser().exists():
                return candidate
        except OSError:
            continue
    return v


def _normalize_single_path_input(raw: str, *, allow_sources: bool = False) -> str:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    if allow_sources and text.lower() in ("@sources", "sources", "authored sources"):
        return "@sources"
    if "\n" in text:
        for line in text.split("\n"):
            candidate = _clean_dropped_path(line)
            if candidate:
                return _recover_existing_path_suffix(candidate)
        return ""
    return _recover_existing_path_suffix(_clean_dropped_path(text))


def _looks_like_sha256_dir_name(name: str) -> bool:
    v = (name or "").strip().lower()
    return len(v) == 64 and all(ch in "0123456789abcdef" for ch in v)


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


def _prepare_drop_actions(
    paths: Sequence[str],
    *,
    seen_keys: Optional[Sequence[Optional[str]]] = None,
    apply_mode: str = "auto",
    max_apply: Optional[int] = None,
) -> List[DropPreparedAction]:
    """
    Resolve dropped paths into actions without mutating AppState.
    Heavy filesystem/hash work lives here so it can run off the UI thread.
    """
    actions: List[DropPreparedAction] = []
    keys = list(seen_keys) if seen_keys is not None else [None] * len(paths)
    mode = str(apply_mode or "auto").strip().lower()
    for idx, v in enumerate(paths):
        seen_key = keys[idx] if idx < len(keys) else None
        if max_apply is not None and idx >= int(max_apply):
            actions.append(
                DropPreparedAction(
                    message=f"Rejected (limit {int(max_apply)} per burst): {_display_path(v, max_len=44)}",
                    terminal=True,
                    seen_key=seen_key,
                )
            )
            continue

        p = Path(v).expanduser()
        if p.exists() and p.is_dir():
            found = _iter_image_files_deterministic(p, limit=250)
            if found and mode in ("auto", "sources"):
                sampled = _sample_photo_import_paths(found, target_count=DEFAULT_ENTROPY_MIN_SOURCES)
                actions.append(
                    DropPreparedAction(
                        message=f"Photo folder sampled: {len(sampled)} of {len(found)} image(s) from {p.name}",
                        success=True,
                        seen_key=seen_key,
                    )
                )
                for fp in sampled:
                    try:
                        sha, size = _sha256_hex_path(fp, max_bytes=100 * 1024 * 1024)
                        actions.append(
                            DropPreparedAction(
                                message=f"Photo source added: {fp.name}",
                                success=True,
                                seen_key=seen_key,
                                source=AuthoredSource(
                                    kind="photo",
                                    name=fp.name,
                                    sha256=sha,
                                    size_bytes=size,
                                    meta={},
                                    path=fp,
                                ),
                            )
                        )
                    except Exception as exc:
                        actions.append(
                            DropPreparedAction(
                                message=f"Photo add failed: {fp.name}: {exc}",
                                success=False,
                                terminal=False,
                                seen_key=seen_key,
                            )
                        )
                if len(found) >= 250:
                    actions.append(
                        DropPreparedAction(
                            message="Photo folder scan capped at 250 images for responsiveness.",
                            success=True,
                            seen_key=seen_key,
                        )
                    )
                continue
            if mode == "sources":
                actions.append(
                    DropPreparedAction(
                        message=f"Not usable as authored sources: {_display_path(p, max_len=44)}. Use Stamp -> Input for whole folders.",
                        success=False,
                        terminal=True,
                        seen_key=seen_key,
                    )
                )
                continue
            resolved = p.resolve()
            actions.append(
                DropPreparedAction(
                    message=f"Input dir set: {_display_path(resolved, max_len=44)}",
                    success=True,
                    seen_key=seen_key,
                    input_dir=resolved,
                )
            )
            continue
        if p.exists() and p.is_file() and _is_image_path(p):
            try:
                sha, size = _sha256_hex_path(p, max_bytes=100 * 1024 * 1024)
                actions.append(
                    DropPreparedAction(
                        message=f"Photo source added: {p.name}",
                        success=True,
                        seen_key=seen_key,
                        source=AuthoredSource(kind="photo", name=p.name, sha256=sha, size_bytes=size, meta={}, path=p),
                    )
                )
                continue
            except Exception as exc:
                actions.append(
                    DropPreparedAction(
                        message=f"Photo add failed: {p.name}: {exc}",
                        success=False,
                        terminal=False,
                        seen_key=seen_key,
                    )
                )
                continue
        if p.exists() and p.is_file() and p.suffix.lower() in (".txt", ".md", ".markdown"):
            try:
                data = p.read_bytes()
                if len(data) > 2_000_000:
                    data = data[:2_000_000]
                txt = data.decode("utf-8", errors="ignore")
                raw = txt.encode("utf-8", errors="ignore")
                sha = hashlib.sha256(raw).hexdigest()
                actions.append(
                    DropPreparedAction(
                        message=f"Text source added: {p.name}",
                        success=True,
                        seen_key=seen_key,
                        source=AuthoredSource(kind="text", name=p.name, sha256=sha, size_bytes=len(raw), text=txt),
                    )
                )
                continue
            except Exception as exc:
                actions.append(
                    DropPreparedAction(
                        message=f"Text add failed: {p.name}: {exc}",
                        success=False,
                        terminal=False,
                        seen_key=seen_key,
                    )
                )
                continue
        actions.append(
            DropPreparedAction(
                message=f"Not usable: {_display_path(p, max_len=44)}",
                success=False,
                terminal=True,
                seen_key=seen_key,
            )
        )
    return actions


def _apply_drop_paths(state: AppState, paths: Sequence[str], *, max_apply: Optional[int] = None) -> List[str]:
    """
    Apply dropped paths: set default input dir and/or import authored sources.
    Returns log lines for what happened.
    """
    actions = _prepare_drop_actions(paths, apply_mode="auto", max_apply=max_apply)
    return _apply_drop_actions_to_state(state, actions, play_sfx=False)


def _apply_drop_actions_to_state(state: AppState, actions: Sequence[DropPreparedAction], *, play_sfx: bool) -> List[str]:
    msgs: List[str] = []
    added_sources = False
    set_input_dir = False
    eligible_before = len(_lockdown_eligible_sources(state.authored_sources))
    for act in actions:
        msgs.append(act.message)
        if act.success:
            if act.input_dir is not None:
                state.last_input_dir = act.input_dir
                set_input_dir = True
            if act.source is not None:
                state.authored_sources.append(act.source)
                added_sources = True
        if act.seen_key is not None and (act.success or act.terminal):
            state.drop_seen.add(act.seen_key)
    if msgs:
        if state.authored_sources:
            state.entropy_selected = max(0, min(state.entropy_selected, len(state.authored_sources) - 1))
        if added_sources:
            _prefer_sources_input_mode(state)
        if set_input_dir:
            _set_current_lane(state, "folder")
            state.stamp_config.input_mode = "folder"
            state.stamp_config.input_path = str(state.last_input_dir or "")
            if state.stamp_panel_draft is not None:
                state.stamp_panel_draft.input_mode = "folder"
                state.stamp_panel_draft.input_path = str(state.last_input_dir or "")
        if added_sources and _mix_ready_crossed(state, before=eligible_before):
            state.reward_ticks = max(state.reward_ticks, 18)
            msgs.append(f"Sources ready for seed: {len(_lockdown_eligible_sources(state.authored_sources))}/{state.entropy_min_sources}.")
        _apply_drop_feedback(state, msgs, play_sfx=play_sfx)
    return msgs


def _start_drop_worker_if_idle(state: AppState) -> None:
    if state.drop_worker_busy or not state.drop_pending_requests:
        return
    req = state.drop_pending_requests.pop(0)
    state.drop_worker_busy = True
    state.status = "Importing drop items..."

    def _worker() -> None:
        actions = _prepare_drop_actions(req.paths, seen_keys=req.seen_keys, apply_mode=req.apply_mode, max_apply=req.max_apply)
        state.drop_results.put((actions, bool(req.play_sfx)))

    threading.Thread(target=_worker, name="eps_drop_import", daemon=True).start()


def _queue_drop_paths(
    state: AppState,
    paths: Sequence[str],
    *,
    seen_keys: Optional[Sequence[Optional[str]]] = None,
    play_sfx: bool,
    apply_mode: str = "auto",
    max_apply: Optional[int] = None,
) -> None:
    if not paths:
        return
    keys = list(seen_keys) if seen_keys is not None else [None] * len(paths)
    state.drop_pending_requests.append(
        DropBatchRequest(paths=[str(p) for p in paths], seen_keys=keys, play_sfx=bool(play_sfx), apply_mode=str(apply_mode), max_apply=max_apply)
    )
    _start_drop_worker_if_idle(state)


def _drain_drop_results(state: AppState) -> None:
    changed = False
    while True:
        try:
            actions, play_sfx = state.drop_results.get_nowait()
        except queue.Empty:
            break
        _apply_drop_actions_to_state(state, actions, play_sfx=play_sfx)
        changed = True
        state.drop_worker_busy = False
    if changed and not state.drop_pending_requests:
        state.status = "Ready."
    _start_drop_worker_if_idle(state)


def _count_drop_success(msgs: Sequence[str]) -> int:
    n = 0
    for m in msgs:
        ml = (m or "").lower()
        if ml.startswith("photo source added:") or ml.startswith("text source added:") or ml.startswith("input dir set:"):
            n += 1
    return n


def _drop_result_is_terminal(msgs: Sequence[str]) -> bool:
    for m in msgs:
        ml = (m or "").lower()
        if ml.startswith("not usable:") or ml.startswith("rejected (limit"):
            return True
    return False


def _current_drop_apply_mode(state: AppState) -> str:
    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    if label == "Sources":
        return "sources"
    if label == "Start" and state.current_lane == "authored":
        return "sources"
    return "folder"


def _mix_ready_crossed(state: AppState, *, before: int) -> bool:
    after = len(_lockdown_eligible_sources(state.authored_sources))
    return before < int(state.entropy_min_sources) <= after


def _trigger_interaction_flash(state: AppState, *, drop_zone: bool = False) -> None:
    """
    Shared visual pulse for entropy-input interactions.
    """
    state.interaction_flash_ticks = max(int(state.interaction_flash_ticks), 18 if drop_zone else 12)
    state.reward_ticks = max(int(state.reward_ticks), 8)
    if drop_zone:
        state.drop_flash_ticks = max(int(state.drop_flash_ticks), 12)


def _apply_drop_feedback(state: AppState, msgs: Sequence[str], *, play_sfx: bool = True) -> int:
    """
    Apply consistent post-import feedback for a drop burst.
    Returns number of successful imports/updates.
    """
    out = list(msgs)
    ok = _count_drop_success(out)
    state.drop_last_msgs = out[:4]
    state.drop_last_msgs_ticks = 40
    if ok > 0:
        state.drop_import_count += int(ok)
        _trigger_interaction_flash(state, drop_zone=True)
        if play_sfx:
            _start_drop_import_sfx_best_effort(duration_s=1.3)
    return ok


def _dropzone_preview(state: AppState, *, width: int, height: int) -> List[str]:
    d = state.drop_dir
    have = len(state.drop_seen)
    lines: List[str] = []
    lines.append("DROP ZONE // drag in files and folders")
    lines.append("")
    lines.append("Use this when you do not want to type paths.")
    lines.append("")
    lines.append("1) Terminal drag-drop / paste (best effort):")
    lines.append("   Press Enter to open a big path field, then drag a folder/file into the terminal.")
    lines.append("   Many terminals will paste the absolute path for you.")
    lines.append("   First 7 paths per burst are processed; extras are rejected.")
    lines.append("")
    lines.append("2) Watched drop folder (deterministic):")
    lines.append(f"   Drop items into: {_display_path(d.resolve() if d.exists() else d, max_len=max(24, width - 18))}")
    lines.append("   Authored Pack will auto-detect new items and import them.")
    lines.append(f"   Items currently in folder: {state.drop_last_count}")
    if state.drop_last_names:
        # Show a few to validate the user dropped into the right place.
        sample = ", ".join(_shorten_middle(name, 18) for name in state.drop_last_names[:5])
        lines.append(f"   Recent items: {sample}")
    lines.append("")
    lines.append("Auto-import rules:")
    lines.append("- Dropped directory: sets default Stamp input dir")
    lines.append("- Dropped image file: added as Authored Source (photo)")
    lines.append("- Dropped .txt/.md: added as Authored Source (text)")
    lines.append("")
    if state.last_input_dir is not None:
        lines.append(f"Current input dir: {_display_path(state.last_input_dir, max_len=max(24, width - 20))}")
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
    lines.append("Tip: Up/Down moves. Enter opens path input. Esc backs out.")

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
    Users can drop items into `state.drop_dir` from a desktop file manager or shell.
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

    # Telemetry for the watched drop folder so users can see whether they're dropping into the right folder.
    try:
        names = [p.name for p in items if p.name not in (".DS_Store",)]
    except Exception:
        names = []
    state.drop_last_count = len(names)
    state.drop_last_names = names[:10]

    unseen_paths: List[str] = []
    unseen_keys: List[str] = []
    for p in items:
        if p.name in (".DS_Store",):
            continue
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in state.drop_seen:
            continue
        unseen_paths.append(str(p))
        unseen_keys.append(key)

    if not unseen_paths:
        return

    accepted = unseen_paths[:7]
    accepted_keys = unseen_keys[:7]
    extras = len(unseen_paths) - len(accepted)
    if extras > 0:
        for key in unseen_keys[7:]:
            state.drop_seen.add(key)
        state.drop_last_msgs = [f"Rejected extras (limit 7 per burst): {extras}"]
        state.drop_last_msgs_ticks = 40

    _queue_drop_paths(
        state,
        accepted,
        seen_keys=accepted_keys,
        play_sfx=bool(state.insane),
        apply_mode=_current_drop_apply_mode(state),
        max_apply=None,
    )


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


def _open_help_doc(state: AppState, key: str) -> None:
    docs = {
        "readme": _REPO_ROOT / "README.md",
    }
    path = docs.get(str(key).strip().lower())
    if path is None:
        state.status = "Failed."
        state.log_lines = [f"Unknown help doc: {key}"]
        return
    open_viewer(state, path.name, _read_text_lines(path))


def _seed_reveal_lines(seed_master: bytes) -> List[str]:
    seed_hex = seed_master.hex()
    seed_b64 = base64.b64encode(seed_master).decode("ascii")
    return [
        "Derived seed material:",
        f"derived_seed.hex: {seed_hex}",
        f"derived_seed.b64: {seed_b64}",
        "",
        "Press Enter, Esc, or q to dismiss.",
    ]


def _show_seed_reveal(state: AppState, seed_master: bytes) -> None:
    open_viewer(state, "Derived Seed Material", _seed_reveal_lines(seed_master))


def _update_receipt_entropy_audit(
    receipt: Dict[str, object],
    *,
    status: str,
    requested_count: int,
    materialized_count: int,
    warnings: Sequence[str],
) -> None:
    receipt["authored_sources_audit_status"] = str(status)
    receipt["authored_sources_audit_requested_count"] = int(requested_count)
    receipt["authored_sources_audit_materialized_count"] = int(materialized_count)
    receipt["authored_sources_audit_warnings"] = [str(w) for w in warnings]


def close_viewer(state: AppState) -> None:
    state.viewer = None
    state.status = "Ready."


def _ui_profile_name(state: AppState) -> str:
    return "Noisy" if state.insane else "Calm"


def _effective_stamp_input(state: AppState, cfg: Optional[StampConfig] = None) -> str:
    cfg = cfg or state.stamp_config
    if cfg.input_mode == "sources":
        return "@sources"
    val = str(cfg.input_path or "").strip()
    if val:
        return val
    if state.last_input_dir is not None:
        return str(state.last_input_dir)
    return ""


def _prefer_sources_input_mode(state: AppState) -> None:
    _set_current_lane(state, "authored")
    state.stamp_config.input_mode = "sources"
    state.stamp_config.input_path = ""
    if state.stamp_panel_draft is not None:
        state.stamp_panel_draft.input_mode = "sources"
        state.stamp_panel_draft.input_path = ""


def _effective_stamp_output(state: AppState, cfg: Optional[StampConfig] = None) -> str:
    cfg = cfg or state.stamp_config
    val = str(cfg.out_path or "").strip()
    if val:
        return val
    if state.last_out_dir is not None:
        return str(state.last_out_dir)
    return "./out"


def _effective_verify_path(state: AppState) -> str:
    val = str(state.verify_config.pack_path or "").strip()
    if val:
        return str(Path(_normalize_single_path_input(val)).expanduser())
    if state.last_pack_dir is not None and state.last_pack_dir.exists():
        return str(state.last_pack_dir)
    if state.last_out_dir is not None and state.last_out_dir.exists():
        return str(state.last_out_dir)
    return ""


def _header_action_for_label(label: str) -> str:
    actions = {
        "Start": "start",
        "Sources": "sources",
        "Stamp": "stamp",
        "Verify": "verify",
        "Help": "help",
    }
    return actions.get(label, "none")


def _quickstart_lines() -> List[str]:
    return [
        "START // choose what to do",
        "",
        "Pack a Folder",
        "- use a folder you already have",
        "- stamp it now, verify it later",
        "",
        "Compose from Sources",
        "- build a pack from photos, notes, or taps",
        "",
        "Verify a Pack",
        "- check a stamped pack folder or zip",
        "",
        "Enter = Pack a Folder",
        "S = Compose from Sources",
        "V = Verify a Pack",
    ]


def _stamp_panel_rows(state: AppState) -> List[StampPanelRow]:
    cfg = state.stamp_panel_draft or state.stamp_config
    if cfg.input_mode == "sources":
        input_label = f"Authored Sources ({len(state.authored_sources)})"
    else:
        input_label = _display_path(_effective_stamp_input(state, cfg) or "not set", max_len=28)
    rows = [
        StampPanelRow("input", "What to pack", input_label),
        StampPanelRow("output", "Save in", _display_path(_effective_stamp_output(state, cfg), max_len=28)),
        StampPanelRow("zip_pack", "Zip copy", "on" if cfg.zip_pack else "off", "toggle"),
        StampPanelRow("derive_seed", "Derive seed", "on" if cfg.derive_seed else "off", "toggle"),
        StampPanelRow("evidence_bundle", "Evidence zip", "on" if cfg.evidence_bundle else "off", "toggle"),
        StampPanelRow("advanced", "More options", "shown" if state.stamp_panel_show_advanced else "hidden", "action"),
    ]
    if state.stamp_panel_show_advanced:
        rows.extend(
            [
                StampPanelRow("pack_id", "Label", _shorten_middle(cfg.pack_id or "-", 28)),
                StampPanelRow("notes", "Note", _shorten_middle(cfg.notes or "-", 28)),
                StampPanelRow("created_at_utc", "Timestamp", _shorten_middle(cfg.created_at_utc or "auto", 28)),
                StampPanelRow("include_hidden", "Hidden files", "on" if cfg.include_hidden else "off", "toggle"),
            ]
        )
        if cfg.input_mode != "sources":
            rows.append(StampPanelRow("exclude_picker", "Exclude picker", "on" if cfg.exclude_picker else "off", "toggle"))
        if cfg.derive_seed and state.authored_sources:
            eligible = len(_lockdown_eligible_sources(state.authored_sources))
            rows.append(
                StampPanelRow(
                    "mix_sources",
                    "Use collected sources in seed",
                    f"{'on' if cfg.mix_sources else 'off'}  Ready for seed {eligible}/{state.entropy_min_sources}",
                    "toggle",
                )
            )
        if cfg.derive_seed:
            rows.extend(
                [
                    StampPanelRow("write_seed", "Write seed files", "on" if cfg.write_seed else "off", "toggle"),
                    StampPanelRow("show_seed", "Reveal seed in UI", "on" if cfg.show_seed else "off", "toggle"),
                    StampPanelRow("write_sources", "Save source record", "on" if cfg.write_sources else "off", "toggle"),
                ]
            )
    rows.append(StampPanelRow("confirm", "Stamp now", "run now", "action"))
    return rows


def _stamp_panel_lines(state: AppState, *, width: int, height: int) -> List[str]:
    rows = _stamp_panel_rows(state)
    if not rows:
        return []
    state.stamp_panel_selected = max(0, min(int(state.stamp_panel_selected), len(rows) - 1))
    lines = [
        "STAMP REVIEW // check settings",
        "Up/Down move  Enter edit  Space toggle  Esc back",
    ]
    for idx, row in enumerate(rows):
        prefix = "> " if idx == state.stamp_panel_selected else "  "
        if row.kind == "action":
            line = f"{prefix}{row.label}: {row.value}"
        else:
            line = f"{prefix}{row.label}: {row.value}"
        lines.append(_shorten_middle(line, max(12, width - 1)))
    lines.append("")
    cfg = state.stamp_panel_draft or state.stamp_config
    lines.append(f"Packing from: {'Authored Sources' if cfg.input_mode == 'sources' else 'Folder'}")
    if cfg.input_mode == "sources":
        lines.append(f"Collected: {len(state.authored_sources)} source{'s' if len(state.authored_sources) != 1 else ''}")
    if cfg.derive_seed and cfg.mix_sources:
        eligible = len(_lockdown_eligible_sources(state.authored_sources))
        lines.append(f"Sources ready for seed: {eligible}/{state.entropy_min_sources}")
        if eligible < int(state.entropy_min_sources):
            lines.append(f"Need {int(state.entropy_min_sources) - eligible} more collected sources to use this option.")
    lines.append("Writes a pack folder with manifest, receipt, and payload.")
    if cfg.derive_seed:
        lines.append("Seed output follows this review choice.")
    else:
        lines.append("Seed output is off.")
    return [ln[:width] for ln in lines[:height]]


def _stamp_preview_lines(state: AppState, *, width: int, height: int) -> List[str]:
    if state.stamp_panel_draft is not None:
        return _stamp_panel_lines(state, width=width, height=height)
    cfg = state.stamp_config
    input_path = _effective_stamp_input(state, cfg) or "not set"
    input_label = "Authored Sources" if cfg.input_mode == "sources" else _display_path(Path(input_path).expanduser() if input_path not in ("", "@sources") else input_path, max_len=44) if input_path not in ("", "@sources") else input_path
    output_label = _display_path(Path(_effective_stamp_output(state, cfg)).expanduser(), max_len=44)
    lines = [
        "STAMP // set what to pack",
        "",
        f"- What to pack: {input_label}",
        f"- Save in: {output_label}",
        f"- Zip copy: {'on' if cfg.zip_pack else 'off'}",
        f"- Derive seed: {'on' if cfg.derive_seed else 'off'}",
    ]
    if cfg.input_mode == "sources":
        lines.append(f"- Collected: {len(state.authored_sources)} source{'s' if len(state.authored_sources) != 1 else ''}")
    if cfg.derive_seed and cfg.mix_sources:
        eligible = len(_lockdown_eligible_sources(state.authored_sources))
        lines.append(f"- Sources ready for seed: {eligible}/{state.entropy_min_sources}")
    lines.extend(
        [
            "",
            "I = choose what to pack",
            "O = choose where to save it",
            "Enter = review and stamp",
            "U/D/Z/E = toggle sources, seed, zip, evidence",
            "",
            "Writes a pack folder and optional zip copy.",
        ]
    )
    return lines[:height]


def _verify_preview_lines(state: AppState) -> List[str]:
    cfg = state.verify_config
    pack_path = _effective_verify_path(state)
    pack_label = _display_path(Path(pack_path).expanduser(), max_len=44) if pack_path else "not set"
    return [
        "VERIFY // check a stamped pack",
        "",
        "Current target:",
        f"- pack: {pack_label}",
        f"- allow large manifest: {'yes' if cfg.allow_large_manifest else 'no'}",
        "",
        "Enter = verify this path",
        "P = choose pack path",
        "L = toggle large-manifest cap",
        "",
        "Checks pack root, payload hashes, and receipt consistency.",
    ]


def _help_summary_lines(_state: AppState) -> List[str]:
    return [
        "HELP // what this tool does",
        "",
        "Authored Pack is a small deterministic pack/verify tool for humans and agents.",
        "",
        "human flow",
        "1. Pack a folder, or compose from sources.",
        "2. Stamp a verifiable pack.",
        "3. Verify it later or after handoff.",
        "",
        "trust boundary",
        "Not an RNG. Noisy mode is ceremony only. Evidence is tamper-evident, not signed.",
        "",
        "more detail",
        "Enter = open this help in a viewer",
        "R = README",
    ]


def _entropy_source_kind_counts(sources: Sequence["AuthoredSource"]) -> Dict[str, int]:
    counts = {"photo": 0, "text": 0, "tap": 0}
    for s in sources:
        kind = str(getattr(s, "kind", "")).lower()
        if kind in counts:
            counts[kind] += 1
    return counts


def _source_collection_line(state: AppState, *, width: int) -> str:
    total = len(state.authored_sources)
    plural = "" if total == 1 else "s"
    line = f"Collected: {total} source{plural}"
    return line[: max(0, int(width))]


def _source_kind_line(state: AppState, *, width: int) -> str:
    counts = _entropy_source_kind_counts(state.authored_sources)
    line = f"Kinds: photo {counts['photo']}  text {counts['text']}  tap {counts['tap']}"
    return line[: max(0, int(width))]


def _selection_preview(state: AppState, label: str, *, width: int, height: int) -> List[str]:
    if label == "Start":
        preview = _quickstart_lines()
    elif label == "Sources":
        preview = _authored_sources_preview(state, width=width, height=height)
    elif label == "Stamp":
        preview = _stamp_preview_lines(state, width=width, height=height)
    elif label == "Verify":
        preview = _verify_preview_lines(state)
    elif label == "Help":
        preview = _help_summary_lines(state)
    else:
        preview = []

    show_result_log = state.status in ("Done.", "Failed.")
    if state.log_lines and show_result_log and (label == "Verify" or (label == "Stamp" and state.stamp_panel_draft is None)):
        preview = state.log_lines[-max(1, (height - 1)) :]
    return [ln[:width] for ln in preview[:height]]


def _draw_header(stdscr, state: AppState, cols: int) -> None:
    head_attr = state.theme.header | (curses.A_REVERSE if state.interaction_flash_ticks > 0 else 0)
    header_identity = build_header_identity_line(APP_NAME, EPS_TUI_TITLE, EPS_TUI_VERSION, cols)
    safe_addstr(stdscr, 0, 0, header_identity[:cols].ljust(cols), head_attr)

    # Keep semantics simple and monotone-safe.
    mode = "offline"
    s = (state.status or "").strip().lower()
    if "fail" in s:
        risk = "WARN"
    elif "done" in s:
        risk = "OK"
    else:
        risk = "INFO"
    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    action = _header_action_for_label(label)
    profile = _ui_profile_name(state).lower()
    status_line = f"MODE: {mode}  PROFILE: {profile}  RISK: {risk}  ACTION: {action}"
    status_attr = state.theme.normal | (curses.A_REVERSE if state.interaction_flash_ticks > 0 else 0)
    safe_addstr(stdscr, 1, 0, status_line[:cols].ljust(cols), status_attr)

    divider = _divider_for_width(cols)
    safe_addstr(stdscr, 2, 0, divider[:cols].ljust(cols), state.theme.normal | curses.A_DIM)


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
    interaction_flash = state.interaction_flash_ticks > 0

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
            attr = bg[idx]
            if drop_flash and (y % 11 == 0) and (x % 19 == 0):
                ch = " "
            if interaction_flash and ((row_seed + x + y + state.tick) % 13 == 0):
                ch = "█"
                attr = _cycle(state.palette.menu_hot, state.tick + x + y, speed=1, default=attr) | curses.A_BOLD
            safe_addstr(stdscr, y, x, (ch * run), attr)
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


def _triangle_wave(phase: float) -> float:
    frac = (phase / (2.0 * math.pi)) % 1.0
    return (4.0 * abs(frac - 0.5)) - 1.0


def _audio_player_command(wav_path: Path) -> Optional[List[str]]:
    """
    Best-effort local WAV playback command.

    macOS:
    - afplay

    Linux:
    - paplay
    - aplay

    No supported local player means silent fallback.
    """
    s = str(wav_path)
    if shutil.which("afplay") is not None:
        return ["afplay", s]
    if sys.platform.startswith("linux"):
        if shutil.which("paplay") is not None:
            return ["paplay", s]
        if shutil.which("aplay") is not None:
            return ["aplay", "-q", s]
    return None


def _play_wav_async_best_effort(
    wav_path: Path,
    *,
    timeout_s: float = 2.0,
    thread_name: str = "eps_sfx",
    token: Optional[str] = None,
    min_interval_s: float = 0.0,
) -> None:
    cmd = _audio_player_command(wav_path)
    if not cmd:
        return
    if token:
        now_ns = time.monotonic_ns()
        min_interval_ns = int(max(0.0, float(min_interval_s)) * 1_000_000_000)
        with _UI_SFX_LOCK:
            last_ns = int(_UI_SFX_LAST_NS.get(token, 0))
            if min_interval_ns and (now_ns - last_ns) < min_interval_ns:
                return
            _UI_SFX_LAST_NS[token] = now_ns

    def _worker() -> None:
        try:
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                p.wait(timeout=max(0.2, float(timeout_s)))
            except Exception:
                try:
                    p.terminate()
                    p.wait(timeout=0.2)
                except Exception:
                    try:
                        p.kill()
                        p.wait(timeout=0.2)
                    except Exception:
                        pass
        except Exception:
            pass

    t = threading.Thread(target=_worker, name=str(thread_name), daemon=True)
    t.start()


def _write_triangle_chord_wav(
    wav_path: Path,
    *,
    duration_s: float,
    freqs_hz: Sequence[float],
    sample_rate: int = 44100,
    amp: float = 0.22,
) -> None:
    dur = max(0.01, min(float(duration_s), 1.0))
    sr = int(sample_rate)
    n = max(1, int(dur * sr))
    phases = [0.0 for _ in freqs_hz]
    f_list = [max(20.0, float(f)) for f in freqs_hz]

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        out = bytearray()
        for i in range(n):
            t = float(i) / float(sr)
            # Clicky attack with fast decay for UI affordance.
            attack = min(1.0, t / 0.004)
            decay = math.exp(-t / max(0.001, dur * 0.24))
            env = attack * decay
            s = 0.0
            for vi, f in enumerate(f_list):
                phases[vi] += (2.0 * math.pi * f) / float(sr)
                s += _triangle_wave(phases[vi])
            if f_list:
                s /= float(len(f_list))
            s *= env
            si = int(max(-32767, min(32767, s * (32767.0 * float(amp)))))
            out += int(si).to_bytes(2, "little", signed=True)
        wf.writeframes(out)


def _ui_sfx_path(kind: str) -> Path:
    k = str(kind).strip().lower()
    with _UI_SFX_LOCK:
        p = _UI_SFX_CACHE.get(k)
        if p is not None and p.is_file():
            return p

        tmp_dir = Path(tempfile.gettempdir())
        p = tmp_dir / f"eps_ui_{os.getpid()}_{k}.wav"
        if k == "move":
            _write_triangle_chord_wav(p, duration_s=0.040, freqs_hz=[600.0], amp=0.20)
        elif k == "select":
            root = 420.0
            fourth = root * (2.0 ** (5.0 / 12.0))
            seventh = root * (2.0 ** (10.0 / 12.0))
            _write_triangle_chord_wav(p, duration_s=0.100, freqs_hz=[root, 880.0, fourth, seventh], amp=0.20)
        else:
            _write_triangle_chord_wav(p, duration_s=0.040, freqs_hz=[640.0], amp=0.18)

        _UI_SFX_CACHE[k] = p
        return p


def _start_ui_move_sfx_best_effort() -> None:
    try:
        p = _ui_sfx_path("move")
        _play_wav_async_best_effort(
            p, timeout_s=0.35, thread_name="eps_ui_move_sfx", token="move", min_interval_s=0.05
        )
    except Exception:
        pass


def _start_ui_select_sfx_best_effort() -> None:
    try:
        p = _ui_sfx_path("select")
        _play_wav_async_best_effort(
            p, timeout_s=0.5, thread_name="eps_ui_select_sfx", token="select", min_interval_s=0.08
        )
    except Exception:
        pass


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


def _write_drop_triangle_wav(
    wav_path: Path,
    *,
    duration_s: float = 1.3,
    hold_hz: float = 25.0,
    lfo_hz: float = 8.0,
    sample_rate: int = 44100,
) -> None:
    """
    Drop-import cue:
    - 3 primary oscillators, each a major second apart
    - plus 3 pointillistic sine "sparkle" oscillators spanning one octave (low->high)
    - sparkle layer is mixed at half amplitude of the primary layer
    - sample-and-hold modulation on pitch (all voices) and burst timing (sparkle voices)
    - 8Hz LFO on pitch
    - upward fifth ramp over the full cue
    """
    dur = max(0.2, min(float(duration_s), 3.0))
    sr = int(sample_rate)
    n = max(1, int(dur * sr))
    hold_n = max(1, int(sr / max(1.0, float(hold_hz))))
    primary_voice_semi = [0.0, 2.0, 4.0]  # major-second spread
    sparkle_voice_semi = [0.0, 6.0, 12.0]  # full-octave spread (low -> high)
    base_hz = 220.0
    sparkle_base_hz = 330.0
    major_fourth = 5.0
    ramp_fifth = 7.0
    rng = random.SystemRandom()
    amp = 0.14
    sparkle_layer_gain = 0.5
    primary_run_detune = float(rng.uniform(-0.25, 0.25))
    sparkle_run_detune = float(rng.uniform(-0.35, 0.35))

    def saw(phase: float) -> float:
        frac = (phase / (2.0 * math.pi)) % 1.0
        return (2.0 * frac) - 1.0

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)

        phase_primary = [0.0, 0.0, 0.0]
        phase_sparkle = [0.0, 0.0, 0.0]
        sh_primary_semi = 0.0
        sh_sparkle_semi = [0.0, 0.0, 0.0]
        sparkle_rem = [0, 0, 0]
        sparkle_len = [1, 1, 1]
        sparkle_amp = [0.0, 0.0, 0.0]
        out = bytearray()
        for i in range(n):
            if i % hold_n == 0:
                sh_primary_semi = float(rng.uniform(-major_fourth, major_fourth))
                sh_sparkle_semi = [float(rng.uniform(-major_fourth, major_fourth)) for _ in sparkle_voice_semi]
                # Pointillistic burst triggers: random short pings per hold frame.
                for vi in range(len(sparkle_voice_semi)):
                    if rng.random() < 0.72:
                        slen = int(max(1, sr * float(rng.uniform(0.010, 0.040))))
                        sparkle_len[vi] = slen
                        sparkle_rem[vi] = slen
                        sparkle_amp[vi] = float(rng.uniform(0.45, 1.0))
                    else:
                        sparkle_rem[vi] = 0
                        sparkle_len[vi] = 1
                        sparkle_amp[vi] = 0.0
            t = float(i) / float(sr)
            prog = float(i) / float(max(1, n - 1))
            lfo_semi = major_fourth * math.sin((2.0 * math.pi * float(lfo_hz) * t))
            ramp_semi = ramp_fifth * prog
            mix_primary = 0.0
            for vi, vsemi in enumerate(primary_voice_semi):
                total_semi = float(vsemi) + primary_run_detune + sh_primary_semi + lfo_semi + ramp_semi
                freq = float(base_hz) * (2.0 ** (total_semi / 12.0))
                phase_primary[vi] += (2.0 * math.pi * freq) / float(sr)
                mix_primary += saw(phase_primary[vi])
            mix_primary /= float(len(primary_voice_semi))

            mix_sparkle = 0.0
            active = 0
            for vi, vsemi in enumerate(sparkle_voice_semi):
                if sparkle_rem[vi] <= 0:
                    continue
                total_semi = float(vsemi) + sparkle_run_detune + sh_sparkle_semi[vi] + (0.75 * lfo_semi) + (0.5 * ramp_semi)
                freq = float(sparkle_base_hz) * (2.0 ** (total_semi / 12.0))
                phase_sparkle[vi] += (2.0 * math.pi * freq) / float(sr)
                # Fast-decay per-burst envelope for pointillistic "sparkle" pings.
                pos = 1.0 - (float(sparkle_rem[vi]) / float(max(1, sparkle_len[vi])))
                burst_env = (1.0 - pos) * (1.0 - pos)
                mix_sparkle += math.sin(phase_sparkle[vi]) * float(sparkle_amp[vi]) * burst_env
                sparkle_rem[vi] -= 1
                active += 1
            if active > 0:
                mix_sparkle /= float(active)

            # Pinball envelope: quick attack with a short release tail.
            a = min(1.0, t / 0.035)
            r = min(1.0, max(0.0, (dur - t) / 0.20))
            env = a * r

            mix = mix_primary + (sparkle_layer_gain * mix_sparkle)
            s = max(-1.0, min(1.0, mix * env))
            si = int(max(-32767, min(32767, s * (32767.0 * amp))))
            out += int(si).to_bytes(2, "little", signed=True)
        wf.writeframes(out)


def _start_drop_import_sfx_best_effort(*, duration_s: float = 1.3) -> None:
    """
    Best-effort drop cue using a local audio player when available.
    Non-fatal if unavailable.
    """
    if not _audio_player_command(Path("eps_drop.wav")):
        return

    tmp_dir = Path(tempfile.gettempdir())
    wav_path = tmp_dir / f"eps_drop_{os.getpid()}_{int(time.time() * 1000)}.wav"

    def _worker() -> None:
        try:
            _write_drop_triangle_wav(wav_path, duration_s=float(duration_s), hold_hz=25.0, lfo_hz=8.0)
            cmd = _audio_player_command(wav_path)
            if not cmd:
                return
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                p.wait(timeout=5)
            except Exception:
                try:
                    p.terminate()
                    p.wait(timeout=0.2)
                except Exception:
                    try:
                        p.kill()
                        p.wait(timeout=0.2)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass

    t = threading.Thread(target=_worker, name="eps_drop_sfx", daemon=True)
    t.start()


def _start_supernova_sfx_best_effort(*, duration_s: float = 5.0) -> None:
    """
    Best-effort audio effect using a local audio player when available.
    Non-fatal if unavailable.
    Runs async to avoid blocking the TUI.
    """
    if not _audio_player_command(Path("eps_supernova.wav")):
        return

    tmp_dir = Path(tempfile.gettempdir())
    wav_path = tmp_dir / f"eps_supernova_{os.getpid()}_{int(time.time())}.wav"
    dur = max(0.5, min(float(duration_s), 10.0))

    def _worker() -> None:
        try:
            _write_modulated_sine_wav(wav_path, duration_s=dur, hold_hz=25.0, f_min_hz=100.0, f_max_hz=1000.0)
            cmd = _audio_player_command(wav_path)
            if not cmd:
                return
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                p.wait(timeout=10)
            except Exception:
                try:
                    p.terminate()
                    p.wait(timeout=0.2)
                except Exception:
                    try:
                        p.kill()
                        p.wait(timeout=0.2)
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
    msg1 = "NO STAMPING WITHOUT SOURCES"
    msg2 = "Add authored sources first (photos/text/tap or imported paths)."

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

    header_identity = build_header_identity_line(
        APP_NAME,
        EPS_TUI_TITLE,
        EPS_TUI_VERSION,
        cols,
        context_suffix=left_tag,
    )
    safe_addstr(stdscr, 0, 0, header_identity[:cols].ljust(cols), head_attr)

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
    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    action = _header_action_for_label(label)
    meta = f" MODE=OFFLINE  PROFILE=NOISY  RISK={risk}  ACTION={action}  STATUS={s or 'Ready'} "
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


def _authored_sources_preview(state: AppState, *, width: int, height: int) -> List[str]:
    """
    Right-pane content for the Sources screen.
    """
    header = "AUTHORED SOURCES // collect what you want in the pack"
    lines: List[str] = [
        header,
        _source_collection_line(state, width=width),
        _source_kind_line(state, width=width),
        "",
    ]
    if state.focus == "entropy" and state.authored_sources:
        lines.append("List focus. Up/Down moves. Enter previews. Tab returns to menu.")
    else:
        lines.append("A add photos  T add text  Space record taps  P import paths")
    lines.append("")
    if not state.authored_sources:
        lines.append("These become the pack contents when you stamp from Authored Sources.")
        lines.append("You can ignore this screen if you just want to pack a normal folder.")
        lines.append("Add a photo, text note, or tap sample.")
        return lines[:height]
    lines.append("When you are ready, go to Stamp and choose Authored Sources.")
    lines.append("")
    if state.drop_import_count > 0:
        lines.append(f"Imported paths this run: {state.drop_import_count}")
        lines.append("")

    sel = max(0, min(int(state.entropy_selected), len(state.authored_sources) - 1))
    seen_ids: set[str] = set()
    for i, s in enumerate(state.authored_sources[: max(0, height - 6)]):
        mark = ">>" if i == sel else "  "
        meta_bits: List[str] = []
        if s.kind == "photo":
            dims = s.meta.get("dims")
            if isinstance(dims, str) and dims:
                meta_bits.append(dims)
        if s.kind == "tap":
            cnt = s.meta.get("events")
            if isinstance(cnt, int):
                meta_bits.append(f"{cnt} taps")
                if int(cnt) < int(LOCKDOWN_MIN_TAP_EVENTS):
                    meta_bits.append("too-short")
        sid = _entropy_source_identity(s)
        if sid in seen_ids:
            meta_bits.append("dupe")
        seen_ids.add(sid)
        meta = (" " + " ".join(meta_bits)) if meta_bits else ""
        lines.append(f"{mark} [{s.kind}] {_shorten_middle(s.name, 28)}  {_fmt_bytes(s.size_bytes)}{meta}")
    return [ln[:width] for ln in lines[:height]]


def _draw_insane_right_pane(stdscr, state: AppState, top: int, left_w: int, cols: int, rows: int) -> None:
    if state.palette is None:
        return
    body_h = rows - top - 1
    right_x = left_w + 1
    right_w = max(0, cols - right_x)

    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    preview = _selection_preview(state, label, width=right_w, height=body_h)

    for i in range(body_h):
        y = top + i
        safe_addstr(stdscr, y, left_w, "║", state.palette.divider)

    for i in range(body_h):
        y = top + i
        line = preview[i] if i < len(preview) else ""
        attr = state.palette.text if i % 2 == 0 else _cycle(state.palette.bg, state.tick + i, speed=4, default=state.palette.text)
        safe_addstr(stdscr, y, right_x, line[:right_w].ljust(right_w), attr)


def _draw_footer(stdscr, state: AppState, rows: int, cols: int) -> None:
    if state.viewer is not None:
        legend = "Up/Down/PgUp/PgDn: scroll  Esc/q/b: back"
        msg = state.status.strip() if state.status else ""
        line = legend
        if msg:
            if len(line) + 2 + len(msg) <= cols:
                line = f"{legend}{' ' * (cols - len(legend) - len(msg))}{msg}"
            else:
                line = f"{legend}  {msg}"
        safe_addstr(stdscr, rows - 1, 0, line[:cols].ljust(cols), state.theme.reverse)
        return

    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    if label == "Sources":
        if state.focus == "entropy" and state.authored_sources:
            legend = "Up/Down: list  Tab: menu  Enter: preview  D: delete  C: clear  Esc: back"
        elif state.authored_sources:
            legend = "Up/Down: menu  Enter: stamp  Tab: list  A/T/Space/P: add  M: mode  Q: quit"
        else:
            legend = "Up/Down: menu  Tab: list  A/T/Space/P: add  M: mode  Q: quit"
    elif label == "Stamp":
        if state.stamp_panel_draft is not None:
            legend = "Up/Down: move  Enter: edit/save  Space: toggle  Esc: back"
        else:
            legend = "Enter: review  I/O: choose paths  U/D/Z/E: toggle  X: more  M: mode"
    elif label == "Verify":
        legend = "Enter: verify  P: path  L: large-manifest  M: mode  Q: quit"
    elif label == "Help":
        legend = "Enter: help  R: README  M: mode  Q: quit"
    elif label == "Start":
        legend = "Enter: pack folder  S: sources  V: verify  M: mode  Q: quit"
    else:
        legend = "Up/Down: move  Enter: select  M: mode  Q: quit"
    msg = state.status.strip() if state.status else ""
    line = legend
    if msg:
        # Right-align the status message when possible.
        if len(line) + 2 + len(msg) <= cols:
            line = f"{legend}{' ' * (cols - len(legend) - len(msg))}{msg}"
        else:
            line = f"{legend}  {msg}"
    safe_addstr(stdscr, rows - 1, 0, line[:cols].ljust(cols), state.theme.reverse)


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
    safe_addstr(stdscr, top, 0, title[:cols].ljust(cols), state.theme.reverse)
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
    preview = _selection_preview(state, label, width=right_w, height=body_h)

    for i in range(body_h):
        y = top + i
        if i >= len(preview):
            line = ""
        else:
            line = preview[i]
        line_attr = state.theme.normal
        if i == 0 and " // " in line:
            line_attr |= curses.A_BOLD
        if line.startswith("> "):
            line_attr |= curses.A_BOLD
        safe_addstr(stdscr, y, left_w, "|", state.theme.normal | curses.A_DIM)
        safe_addstr(stdscr, y, right_x, line[:right_w].ljust(right_w), line_attr)


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
            input("\n(Authored Pack) Press Enter to return... ")
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


def _prompt_str_curses(stdscr, label: str, *, default: str = "", max_len: int = 512) -> Optional[str]:
    rows, cols = stdscr.getmaxyx()
    lower_label = str(label).lower()
    show_default = str(default)
    is_path_like = any(tok in lower_label for tok in ("path", "dir", "folder", "file", "@sources"))
    if default and is_path_like:
        max_default = max(12, min(32, max(12, cols // 3)))
        show_default = _display_path(default, max_len=max_default)
    title = str(label).replace("(Authored Pack) ", "").strip()
    prompt = f"Value [{show_default}]: " if default else "Value: "
    effective_max_len = int(max_len)
    if effective_max_len <= 512 and is_path_like:
        effective_max_len = 4096
    title_y = max(0, rows - 3)
    hint_y = max(0, rows - 2)
    input_y = max(0, rows - 1)
    buf: List[str] = []
    cursor = 0
    truncated = False
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        stdscr.nodelay(False)
        stdscr.timeout(-1)
    except curses.error:
        pass
    try:
        while True:
            current = "".join(buf)
            visible_w = max(0, cols - len(prompt) - 1)
            visible = current
            cursor_x = len(prompt) + cursor
            if visible_w > 0 and len(visible) > visible_w:
                start = max(0, cursor - visible_w + 1)
                end = start + visible_w
                visible = visible[start:end]
                cursor_x = len(prompt) + max(0, cursor - start)
            for y in {title_y, hint_y, input_y}:
                stdscr.move(y, 0)
                stdscr.clrtoeol()
            safe_addstr(stdscr, title_y, 0, f"Editing {title}"[:cols].ljust(cols), curses.A_REVERSE | curses.A_BOLD)
            hint = "Type a value. Enter saves. Esc cancels. Ctrl-A/E move. Ctrl-U/W erase."
            if is_path_like:
                hint = "Type a value. Enter saves. Esc cancels. Single q also cancels path prompts. Ctrl-A/E move. Ctrl-U/W erase."
            safe_addstr(stdscr, hint_y, 0, hint[:cols].ljust(cols), curses.A_REVERSE)
            safe_addstr(stdscr, input_y, 0, prompt[:cols], curses.A_REVERSE)
            if visible_w > 0:
                safe_addstr(stdscr, input_y, len(prompt), visible[:visible_w], curses.A_REVERSE)
            if truncated and cols > 8:
                trunc_hint = " [truncated]"
                safe_addstr(stdscr, input_y, max(0, cols - len(trunc_hint)), trunc_hint[:cols], curses.A_REVERSE)
            try:
                stdscr.move(input_y, min(max(0, cursor_x), max(0, cols - 1)))
            except curses.error:
                pass
            stdscr.refresh()

            try:
                ch = stdscr.getch()
            except curses.error:
                ch = -1
            if ch in (27,):
                return None
            if ch in (10, 13, curses.KEY_ENTER):
                out = "".join(buf).strip()
                out = out if out else str(default)
                if out in ("q", "Q") and any(tok in lower_label for tok in ("path", "dir", "folder", "file")):
                    return None
                return out
            if ch == 1:
                cursor = 0
                continue
            if ch == 5:
                cursor = len(buf)
                continue
            if ch == 21:
                if cursor > 0:
                    del buf[:cursor]
                    cursor = 0
                continue
            if ch == 23:
                if cursor > 0:
                    cut = cursor
                    while cut > 0 and buf[cut - 1].isspace():
                        cut -= 1
                    while cut > 0 and not buf[cut - 1].isspace():
                        cut -= 1
                    while cut > 0 and buf[cut - 1].isspace():
                        cut -= 1
                    del buf[cut:cursor]
                    cursor = cut
                continue
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                if cursor > 0:
                    del buf[cursor - 1]
                    cursor -= 1
                continue
            if ch == curses.KEY_DC:
                if cursor < len(buf):
                    del buf[cursor]
                continue
            if ch == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
                continue
            if ch == curses.KEY_RIGHT:
                cursor = min(len(buf), cursor + 1)
                continue
            if ch == curses.KEY_HOME:
                cursor = 0
                continue
            if ch == curses.KEY_END:
                cursor = len(buf)
                continue
            if 32 <= ch <= 126:
                if len(buf) >= effective_max_len:
                    truncated = True
                    continue
                buf.insert(cursor, chr(int(ch)))
                cursor += 1
                continue
    finally:
        for y in {title_y, hint_y, input_y}:
            try:
                stdscr.move(y, 0)
                stdscr.clrtoeol()
            except curses.error:
                pass
        try:
            stdscr.refresh()
        except curses.error:
            pass
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            stdscr.nodelay(False)
            stdscr.timeout(100)
        except curses.error:
            pass


def _prompt_bool_curses(stdscr, label: str, *, default: bool = False) -> Optional[bool]:
    d = "y" if default else "n"
    raw = _prompt_str_curses(stdscr, f"{label} (y/n)", default=d, max_len=5)
    if raw is None:
        return None
    s = raw.strip().lower()
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
    p_s = _prompt_str_curses(stdscr, "(Authored Pack) photo path (file or dir)", default=".")
    if p_s is None:
        state.status = "Ready."
        state.log_lines = ["Add photos cancelled."]
        return
    p = Path(_normalize_single_path_input(p_s)).expanduser()
    if not p.exists():
        state.status = "Failed."
        state.log_lines = [f"Authored source add failed: not found: {p}"]
        return
    state.status = "Scanning photos..."
    state.log_lines = [f"source: {_display_path(p, max_len=44)}"]
    draw(stdscr, state)
    eligible_before = len(_lockdown_eligible_sources(state.authored_sources))
    paths: List[Path] = []
    sampled_from_dir = False
    total_found = 0
    if p.is_file():
        paths = [p]
    elif p.is_dir():
        found = _iter_image_files_deterministic(p, limit=250)
        total_found = len(found)
        paths = _sample_photo_import_paths(found, target_count=int(state.entropy_min_sources))
        sampled_from_dir = True
    else:
        state.status = "Failed."
        state.log_lines = [f"Authored source add failed: not a file/dir: {p}"]
        return

    added = 0
    total = len(paths)
    for idx, fp in enumerate(paths, start=1):
        if idx == 1 or idx % 20 == 0 or idx == total:
            state.status = f"Scanning photos... {idx}/{total}"
            draw(stdscr, state)
        try:
            sha, size = _sha256_hex_path(fp, max_bytes=100 * 1024 * 1024)
        except Exception:
            continue
        name = fp.name
        state.authored_sources.append(
            AuthoredSource(kind="photo", name=name, sha256=sha, size_bytes=size, meta={}, path=fp)
        )
        added += 1

    if added:
        _prefer_sources_input_mode(state)
        _trigger_interaction_flash(state, drop_zone=False)
        if _mix_ready_crossed(state, before=eligible_before):
            state.reward_ticks = max(state.reward_ticks, 18)
        state.status = "Done."
        if sampled_from_dir:
            state.log_lines = [f"Added {added} photo source(s) sampled from {total_found} image(s)."]
        else:
            state.log_lines = [f"Added {added} photo source(s)."]
        if _mix_ready_crossed(state, before=eligible_before):
            state.log_lines.append(f"Sources ready for seed: {len(_lockdown_eligible_sources(state.authored_sources))}/{state.entropy_min_sources}.")
        if total_found >= 250:
            state.log_lines.append("Scan capped at 250 images for responsiveness.")
    else:
        state.status = "Failed."
        state.log_lines = ["No valid images found or hash failed."]


def _action_entropy_add_text(state: AppState, stdscr) -> None:
    label = _prompt_str_curses(stdscr, "(Authored Pack) text label", default="note")
    if label is None:
        state.status = "Ready."
        state.log_lines = ["Add text cancelled."]
        return
    text = _prompt_str_curses(stdscr, "(Authored Pack) text (one line)", default="", max_len=4096)
    if text is None:
        state.status = "Ready."
        state.log_lines = ["Add text cancelled."]
        return
    raw = text.encode("utf-8", errors="ignore")
    sha = hashlib.sha256(raw).hexdigest()
    eligible_before = len(_lockdown_eligible_sources(state.authored_sources))
    state.authored_sources.append(
        AuthoredSource(kind="text", name=label.strip() or "note", sha256=sha, size_bytes=len(raw), text=text)
    )
    _prefer_sources_input_mode(state)
    _trigger_interaction_flash(state, drop_zone=False)
    if _mix_ready_crossed(state, before=eligible_before):
        state.reward_ticks = max(state.reward_ticks, 18)
    state.status = "Done."
    state.log_lines = [f"Added text source: {label.strip() or 'note'} ({_fmt_bytes(len(raw))})."]
    if _mix_ready_crossed(state, before=eligible_before):
        state.log_lines.append(f"Sources ready for seed: {len(_lockdown_eligible_sources(state.authored_sources))}/{state.entropy_min_sources}.")


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
                state.status = f"Tap sample: {count}/{target} ..."
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
            state.status = f"Tap sample: {count}/{target} ..."
            draw(stdscr, state)
    finally:
        try:
            stdscr.nodelay(False)
            stdscr.timeout(50 if state.insane else 100)
        except curses.error:
            pass

    digest = h.hexdigest()
    if count < int(LOCKDOWN_MIN_TAP_EVENTS):
        state.status = "Failed."
        state.log_lines = [
            "Tap sample rejected: too short.",
            f"Needs {LOCKDOWN_MIN_TAP_EVENTS}+ taps to count for derived seed.",
        ]
        return
    meta: Dict[str, object] = {"events": int(count), "sample": sample_events[:16]}
    # size_bytes tracks the conceptual size of the captured timing/code stream.
    eligible_before = len(_lockdown_eligible_sources(state.authored_sources))
    state.authored_sources.append(AuthoredSource(kind="tap", name="tap", sha256=digest, size_bytes=count * 8, meta=meta))
    _prefer_sources_input_mode(state)
    _trigger_interaction_flash(state, drop_zone=False)
    state.reward_ticks = max(state.reward_ticks, 18)
    state.status = "Done."
    state.log_lines = [
        f"Added tap source: {count} events.",
        f"tap_sha256: {digest}",
    ]
    if _mix_ready_crossed(state, before=eligible_before):
        state.log_lines.append(f"Sources ready for seed: {len(_lockdown_eligible_sources(state.authored_sources))}/{state.entropy_min_sources}.")


def _action_entropy_delete_selected(state: AppState) -> None:
    if not state.authored_sources:
        return
    idx = max(0, min(int(state.entropy_selected), len(state.authored_sources) - 1))
    removed = state.authored_sources.pop(idx)
    state.entropy_selected = max(0, min(state.entropy_selected, len(state.authored_sources) - 1))
    if not state.authored_sources:
        state.focus = "menu"
    state.status = "Done."
    state.log_lines = [f"Removed source: [{removed.kind}] {removed.name}."]


def _action_entropy_clear(state: AppState) -> None:
    n = len(state.authored_sources)
    state.authored_sources.clear()
    state.entropy_selected = 0
    state.focus = "menu"
    state.stamp_config.mix_sources = False
    if state.stamp_panel_draft is not None:
        state.stamp_panel_draft.mix_sources = False
    state.status = "Done."
    state.log_lines = [f"Cleared {n} source(s)."]


def _action_entropy_preview(state: AppState) -> None:
    if not state.authored_sources:
        return
    idx = max(0, min(int(state.entropy_selected), len(state.authored_sources) - 1))
    s = state.authored_sources[idx]
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
    open_viewer(state, f"Authored Source: {s.kind}", lines)


def _prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{label} [{suffix}]: ").strip().lower()
    if not raw:
        return bool(default)
    return raw in ("y", "yes", "true", "1")


def _validated_photo_source_path(s: AuthoredSource, *, max_bytes: int = 100 * 1024 * 1024) -> Path:
    if s.path is None or not s.path.is_file():
        raise ValueError(f"photo source missing or unreadable: {s.name}")
    sha, size = _sha256_hex_path(s.path, max_bytes=max_bytes)
    if sha != s.sha256 or int(size) != int(s.size_bytes):
        raise ValueError(f"photo source drift detected: {s.name}")
    return s.path


def _build_sources_payload_dir(sources: Sequence[AuthoredSource]) -> Path:
    """
    Materialize staged authored sources into a real directory so they can be stamped as artifacts.
    Photos are copied; text/tap become files. The caller owns cleanup.
    """
    td = Path(tempfile.mkdtemp(prefix="eps_payload_sources_"))
    try:
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
                try:
                    src = _validated_photo_source_path(s)
                    dst = td / "photos" / f"{i:03d}_{src.name}"
                    shutil.copy2(src, dst)
                except Exception as exc:
                    raise ValueError(f"failed to materialize photo source '{s.name}': {exc}") from exc
                entry["path"] = str(Path("photos") / dst.name)
            elif s.kind == "photo":
                raise ValueError(f"photo source missing or unreadable: {s.name}")
            elif s.kind == "text" and s.text is not None:
                dst = td / "text" / f"{i:03d}_{re.sub(r'[^A-Za-z0-9._-]+', '_', s.name)[:40] or 'note'}.txt"
                dst.write_text(s.text, encoding="utf-8", errors="ignore")
                try:
                    dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
                entry["path"] = str(Path("text") / dst.name)
            elif s.kind == "text":
                raise ValueError(f"text source missing content: {s.name}")
            elif s.kind == "tap":
                dst = td / "tap" / f"{i:03d}_tap.json"
                dst.write_text(json.dumps(entry, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                try:
                    dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
                entry["path"] = str(Path("tap") / dst.name)
            else:
                raise ValueError(f"unsupported entropy source kind: {s.kind}")
            index.append(entry)

        (td / "sources.json").write_text(json.dumps(index, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return td
    except Exception:
        try:
            shutil.rmtree(td)
        except Exception:
            pass
        raise


def _write_authored_sources_into_pack(pack_dir: Path, sources: Sequence[AuthoredSource]) -> Tuple[Optional[Path], List[str], int]:
    """
    Persist staged sources into the pack directory (outside payload/) for audit.
    These files are excluded from authored_pack.zip.
    """
    warnings: List[str] = []
    out = pack_dir / "authored_sources"
    tmp_out: Optional[Path] = None
    try:
        out.mkdir(parents=True, exist_ok=True)
        tmp_out = Path(tempfile.mkdtemp(prefix=".eps_authored_sources_", dir=str(pack_dir)))
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
            if s.kind == "photo":
                try:
                    src = _validated_photo_source_path(s)
                    dst = tmp_out / f"{i:03d}_{src.name}"
                    shutil.copy2(src, dst)
                except Exception as exc:
                    warnings.append(f"skipped photo source '{s.name}': {exc}")
                    continue
                entry["path"] = dst.name
            elif s.kind == "text" and s.text is not None:
                dst = tmp_out / f"{i:03d}_{re.sub(r'[^A-Za-z0-9._-]+', '_', s.name)[:40] or 'note'}.txt"
                try:
                    dst.write_text(s.text, encoding="utf-8", errors="ignore")
                except Exception as exc:
                    warnings.append(f"skipped text source '{s.name}': {exc}")
                    continue
                try:
                    dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
                entry["path"] = dst.name
            elif s.kind == "tap":
                dst = tmp_out / f"{i:03d}_tap.json"
                try:
                    dst.write_text(json.dumps(entry, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                except Exception as exc:
                    warnings.append(f"skipped tap source '{s.name}': {exc}")
                    continue
                try:
                    dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
                entry["path"] = dst.name
            else:
                warnings.append(f"skipped unsupported entropy source kind '{s.kind}': {s.name}")
                continue
            index.append(entry)

        if not index:
            warnings.append("authored_sources audit produced no materialized entries")
            return None, warnings, 0

        (tmp_out / "sources.index.json").write_text(json.dumps(index, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if out.exists():
            try:
                if out.is_dir():
                    shutil.rmtree(out)
                else:
                    out.unlink()
            except Exception as exc:
                warnings.append(f"could not replace existing authored_sources audit dir: {exc}")
                return None, warnings, 0
        tmp_out.replace(out)
        tmp_out = None
        return out, warnings, len(index)
    except Exception as exc:
        warnings.append(f"authored_sources audit failed: {exc}")
        return None, warnings, 0
    finally:
        if tmp_out is not None:
            try:
                shutil.rmtree(tmp_out)
            except Exception:
                pass


def _stamp_panel_index(state: AppState, key: str) -> int:
    rows = _stamp_panel_rows(state)
    for idx, row in enumerate(rows):
        if row.key == key:
            return idx
    return 0


def _open_stamp_panel(state: AppState, selected_key: Optional[str] = None, *, show_advanced: bool = False) -> None:
    state.stamp_panel_draft = replace(state.stamp_config)
    state.stamp_panel_show_advanced = bool(show_advanced)
    state.stamp_panel_selected = 0
    if selected_key is not None:
        state.stamp_panel_selected = _stamp_panel_index(state, selected_key)
    state.log_lines = []
    if selected_key is None:
        state.status = "Review open."
    else:
        state.status = f"Editing {selected_key.replace('_', ' ')}."


def _close_stamp_panel(state: AppState) -> None:
    state.stamp_panel_draft = None
    state.stamp_panel_selected = 0
    state.stamp_panel_show_advanced = False
    state.status = "Ready."


def _stamp_panel_cfg(state: AppState) -> StampConfig:
    return state.stamp_panel_draft if state.stamp_panel_draft is not None else state.stamp_config


def _edit_stamp_text_row(state: AppState, stdscr, key: str) -> None:
    cfg = _stamp_panel_cfg(state)
    prompts = {
        "pack_id": "(Authored Pack) label (optional)",
        "notes": "(Authored Pack) note (optional)",
        "created_at_utc": "(Authored Pack) timestamp UTC (optional)",
    }
    defaults = {
        "pack_id": cfg.pack_id,
        "notes": cfg.notes,
        "created_at_utc": cfg.created_at_utc,
    }
    prompt = prompts.get(key)
    if prompt is None:
        return
    raw = _prompt_str_curses(stdscr, prompt, default=defaults.get(key, ""))
    if raw is None:
        state.status = "Ready."
        return
    setattr(cfg, key, raw.strip())
    state.log_lines = []
    state.status = "Stamp field updated."


def _toggle_stamp_panel_value(state: AppState, key: str) -> None:
    cfg = _stamp_panel_cfg(state)
    if key == "zip_pack":
        cfg.zip_pack = not cfg.zip_pack
        state.status = f"Zip pack: {'on' if cfg.zip_pack else 'off'}."
    elif key == "derive_seed":
        cfg.derive_seed = not cfg.derive_seed
        if not cfg.derive_seed:
            cfg.mix_sources = False
            cfg.write_seed = False
            cfg.show_seed = False
            cfg.write_sources = False
        state.status = f"Derive seed: {'on' if cfg.derive_seed else 'off'}."
    elif key == "evidence_bundle":
        cfg.evidence_bundle = not cfg.evidence_bundle
        state.status = f"Evidence bundle: {'on' if cfg.evidence_bundle else 'off'}."
    elif key == "include_hidden":
        cfg.include_hidden = not cfg.include_hidden
        state.status = f"Hidden files: {'on' if cfg.include_hidden else 'off'}."
    elif key == "exclude_picker":
        cfg.exclude_picker = not cfg.exclude_picker
        state.status = f"Exclude picker: {'on' if cfg.exclude_picker else 'off'}."
    elif key == "mix_sources":
        cfg.mix_sources = not cfg.mix_sources
        state.status = f"Use collected sources in seed: {'on' if cfg.mix_sources else 'off'}."
    elif key == "write_seed":
        cfg.write_seed = not cfg.write_seed
        state.status = f"Write seed files: {'on' if cfg.write_seed else 'off'}."
    elif key == "show_seed":
        cfg.show_seed = not cfg.show_seed
        state.status = f"Reveal seed in UI: {'on' if cfg.show_seed else 'off'}."
    elif key == "write_sources":
        cfg.write_sources = not cfg.write_sources
        state.status = f"Save source record: {'on' if cfg.write_sources else 'off'}."


def _activate_stamp_panel_row(state: AppState, stdscr) -> None:
    if state.stamp_panel_draft is None:
        return
    rows = _stamp_panel_rows(state)
    if not rows:
        return
    state.stamp_panel_selected = max(0, min(int(state.stamp_panel_selected), len(rows) - 1))
    row = rows[state.stamp_panel_selected]
    cfg = state.stamp_panel_draft
    if row.key == "input":
        _edit_stamp_input(state, stdscr, cfg)
    elif row.key == "output":
        _edit_stamp_output(state, stdscr, cfg)
    elif row.key in ("pack_id", "notes", "created_at_utc"):
        _edit_stamp_text_row(state, stdscr, row.key)
    elif row.key == "advanced":
        state.stamp_panel_show_advanced = not state.stamp_panel_show_advanced
        state.status = f"Advanced rows: {'shown' if state.stamp_panel_show_advanced else 'hidden'}."
    elif row.key == "confirm":
        state.stamp_config = replace(cfg)
        state.stamp_panel_draft = None
        state.stamp_panel_selected = 0
        state.stamp_panel_show_advanced = False
        _run_stamp_from_config(state, stdscr)
        return
    elif row.kind == "toggle":
        _toggle_stamp_panel_value(state, row.key)
    state.stamp_panel_selected = max(0, min(int(state.stamp_panel_selected), len(_stamp_panel_rows(state)) - 1))


def _action_sources_import_paths(state: AppState, stdscr) -> None:
    _set_current_lane(state, "authored")
    raw = _prompt_str_curses(stdscr, "(Authored Pack) import files or folders", default=str(state.last_input_dir or ""), max_len=4096)
    if raw is None:
        state.status = "Ready."
        state.log_lines = ["Import cancelled."]
        return
    paths = _split_drop_payload(raw)
    if not paths:
        state.status = "Ready."
        state.log_lines = ["No paths supplied."]
        return
    actions = _prepare_drop_actions(paths, apply_mode="sources", max_apply=7)
    msgs = _apply_drop_actions_to_state(state, actions, play_sfx=False)
    if not msgs:
        state.status = "Ready."
        state.log_lines = ["No usable paths found."]
        return
    ok = _count_drop_success(msgs)
    state.status = "Done." if ok > 0 else "Failed."
    state.log_lines = list(msgs[:6])


def _edit_stamp_input(state: AppState, stdscr, cfg: Optional[StampConfig] = None, *, allow_sources: bool = True) -> bool:
    cfg = cfg or state.stamp_config
    if allow_sources:
        default = "Authored Sources" if cfg.input_mode == "sources" else (_effective_stamp_input(state, cfg) or ".")
        prompt = "(Authored Pack) choose folder to pack or Authored Sources"
    else:
        default = _effective_stamp_input(state, cfg) or "."
        prompt = "(Authored Pack) choose folder to pack"
    raw = _prompt_str_curses(stdscr, prompt, default=default)
    if raw is None:
        state.status = "Ready."
        return False
    normalized = _normalize_single_path_input(raw, allow_sources=allow_sources)
    if normalized == "@sources":
        _set_current_lane(state, "authored")
        cfg.input_mode = "sources"
        cfg.input_path = ""
    else:
        _set_current_lane(state, "folder")
        cfg.input_mode = "folder"
        cfg.input_path = normalized
    state.log_lines = []
    state.status = "What to pack updated."
    return True


def _edit_stamp_output(state: AppState, stdscr, cfg: Optional[StampConfig] = None) -> bool:
    cfg = cfg or state.stamp_config
    raw = _prompt_str_curses(stdscr, "(Authored Pack) choose output folder", default=_effective_stamp_output(state, cfg))
    if raw is None:
        state.status = "Ready."
        return False
    cfg.out_path = _normalize_single_path_input(raw) or "./out"
    state.log_lines = []
    state.status = "Save folder updated."
    return True


def _edit_stamp_advanced(state: AppState, stdscr, cfg: Optional[StampConfig] = None) -> None:
    cfg = cfg or state.stamp_config
    pack_id = _prompt_str_curses(stdscr, "(Authored Pack) label (optional)", default=cfg.pack_id)
    if pack_id is None:
        state.status = "Ready."
        return
    notes = _prompt_str_curses(stdscr, "(Authored Pack) note (optional)", default=cfg.notes)
    if notes is None:
        state.status = "Ready."
        return
    created_at = _prompt_str_curses(stdscr, "(Authored Pack) timestamp UTC (optional)", default=cfg.created_at_utc)
    if created_at is None:
        state.status = "Ready."
        return
    include_hidden = _prompt_bool_curses(stdscr, "(Authored Pack) include hidden files", default=cfg.include_hidden)
    if include_hidden is None:
        state.status = "Ready."
        return
    exclude_picker = cfg.exclude_picker
    if cfg.input_mode != "sources":
        exclude_picker = _prompt_bool_curses(stdscr, "(Authored Pack) pick files to exclude before stamp", default=cfg.exclude_picker)
        if exclude_picker is None:
            state.status = "Ready."
            return
    write_seed = cfg.write_seed
    show_seed = cfg.show_seed
    mix_sources = cfg.mix_sources
    write_sources = cfg.write_sources
    if cfg.derive_seed:
        if state.authored_sources:
            mix_sources_v = _prompt_bool_curses(stdscr, "(Authored Pack) use staged sources in seed", default=cfg.mix_sources)
            if mix_sources_v is None:
                state.status = "Ready."
                return
            mix_sources = bool(mix_sources_v)
        write_seed_v = _prompt_bool_curses(stdscr, "(Authored Pack) write seed files (chmod 600)", default=cfg.write_seed)
        if write_seed_v is None:
            state.status = "Ready."
            return
        show_seed_v = _prompt_bool_curses(stdscr, "(Authored Pack) show seed in UI", default=cfg.show_seed)
        if show_seed_v is None:
            state.status = "Ready."
            return
        write_sources_v = _prompt_bool_curses(
            stdscr,
            "(Authored Pack) save source record in pack",
            default=cfg.write_sources or bool(mix_sources),
        )
        if write_sources_v is None:
            state.status = "Ready."
            return
        write_seed = bool(write_seed_v)
        show_seed = bool(show_seed_v)
        write_sources = bool(write_sources_v)
    cfg.pack_id = pack_id.strip()
    cfg.notes = notes.strip()
    cfg.created_at_utc = created_at.strip()
    cfg.include_hidden = bool(include_hidden)
    cfg.exclude_picker = bool(exclude_picker)
    cfg.mix_sources = bool(mix_sources)
    cfg.write_seed = bool(write_seed)
    cfg.show_seed = bool(show_seed)
    cfg.write_sources = bool(write_sources)
    state.status = "Stamp options updated."


def _edit_verify_path(state: AppState, stdscr) -> bool:
    raw = _prompt_str_curses(stdscr, "(Authored Pack) pack path (dir or .zip)", default=_effective_verify_path(state) or "./out")
    if raw is None:
        state.status = "Ready."
        return False
    state.verify_config.pack_path = _normalize_single_path_input(raw)
    state.log_lines = []
    state.status = "Verify target updated."
    return True


def _run_stamp_plan(
    state: AppState,
    stdscr,
    *,
    input_s: str,
    out_s: str,
    pack_id_s: str,
    notes_s: str,
    created_at_s: str,
    include_hidden: bool,
    exclude_picker: bool,
    zip_pack: bool,
    derive_seed: bool,
    mix_sources: bool,
    write_seed: bool,
    show_seed: bool,
    write_sources: bool,
    evidence_bundle: bool,
) -> None:
    state.status = "Stamp: review..."
    state.log_lines = []

    tmp_payload_dir: Optional[Path] = None
    sources_for_audit: Sequence[AuthoredSource] = []
    audit_status = "not_requested"
    audit_requested_count = 0
    audit_materialized_count = 0
    audit_warnings: List[str] = []
    audit_dir_path: Optional[Path] = None
    mixed_sources: List[AuthoredSource] = []
    pool_sha = None
    seed_path_label: Optional[str] = None

    try:
        input_dir: Path
        exclude_relpaths: Optional[Set[str]] = None
        input_choice = _normalize_single_path_input(input_s.strip(), allow_sources=True)
        if input_choice == "@sources":
            if not state.authored_sources:
                state.status = "Failed."
                state.log_lines = ["Authored Sources selected, but none are staged yet.", "Go to Sources and add or import some first."]
                try:
                    state.selected = state.menu.index("Sources")
                except ValueError:
                    state.selected = 0
                state.focus = "menu"
                return
            input_dir = _build_sources_payload_dir(state.authored_sources)
            tmp_payload_dir = input_dir
            _set_current_lane(state, "authored")
            state.stamp_config.input_mode = "sources"
            state.stamp_config.input_path = ""
        else:
            input_dir = Path(input_choice).expanduser()
            _set_current_lane(state, "folder")
            state.stamp_config.input_mode = "folder"
            state.stamp_config.input_path = input_choice
            state.last_input_dir = input_dir.resolve()
            if exclude_picker:
                picked = _artifact_exclude_picker(stdscr, state, input_dir=input_dir, include_hidden=bool(include_hidden))
                if picked is None:
                    state.status = "Ready."
                    state.log_lines = ["Artifact exclude picker cancelled."]
                    return
                exclude_relpaths = set(picked)

        out_dir = Path(_normalize_single_path_input(out_s)).expanduser()
        pack_id = pack_id_s.strip() or None
        notes = notes_s.strip() or None
        created_at = created_at_s.strip() or None

        if derive_seed and state.authored_sources and mix_sources:
            mixed_sources = _lockdown_eligible_sources(state.authored_sources)
            if len(mixed_sources) < int(state.entropy_min_sources):
                need_more = int(state.entropy_min_sources) - len(mixed_sources)
                state.status = "Failed."
                state.log_lines = [
                    "Stamp blocked: staged sources are not ready to mix into the seed.",
                    f"Sources ready for seed: {len(mixed_sources)}/{state.entropy_min_sources}",
                    f"Need {need_more} more collected sources to use this option.",
                    f"Tap sources need >= {LOCKDOWN_MIN_TAP_EVENTS} key events.",
                ]
                try:
                    state.selected = state.menu.index("Sources")
                except ValueError:
                    state.selected = 0
                state.focus = "menu"
                return
            seed_path_label = "staged sources in seed"
            pool_sha = _entropy_pool_sha256(mixed_sources)
        elif derive_seed and mix_sources:
            state.status = "Failed."
            state.log_lines = [
                "Stamp blocked: staged sources were requested for the seed, but none are available.",
                "Go to Sources and add or import some first.",
            ]
            try:
                state.selected = state.menu.index("Sources")
            except ValueError:
                state.selected = 0
            state.focus = "menu"
            return
        elif derive_seed:
            seed_path_label = "root-only seed"

        sources_for_audit = mixed_sources if mix_sources else state.authored_sources

        def _before_finalize(pack_dir: Path) -> Optional[Dict[str, object]]:
            nonlocal audit_status, audit_requested_count, audit_materialized_count, audit_warnings, audit_dir_path
            audit_status = "not_requested"
            audit_requested_count = 0
            audit_materialized_count = 0
            audit_warnings = []
            audit_dir_path = None
            if write_sources and derive_seed and sources_for_audit:
                p, warnings, materialized_count = _write_authored_sources_into_pack(pack_dir, sources_for_audit)
                audit_requested_count = len(sources_for_audit)
                audit_materialized_count = int(materialized_count)
                audit_warnings = list(warnings)
                audit_dir_path = p
                if p is None:
                    audit_status = "failed"
                elif audit_warnings:
                    audit_status = "partial"
                else:
                    audit_status = "ok"
            fields: Dict[str, object] = {}
            _update_receipt_entropy_audit(
                fields,
                status=audit_status,
                requested_count=audit_requested_count,
                materialized_count=audit_materialized_count,
                warnings=audit_warnings,
            )
            return fields

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
                authored_sources_sha256=pool_sha if mix_sources else None,
                evidence_bundle=evidence_bundle,
                write_seed_files=write_seed,
                print_seed=False,
                before_finalize=_before_finalize,
            )

        if state.insane:
            res = _stamp_with_insane_fx(stdscr, state, _do_stamp, min_stamping_s=5.0, created_s=5.0)
        else:
            state.status = "Stamping..."
            state.log_lines = ["Building pack..."]
            draw(stdscr, state)
            res = _do_stamp()
    except Exception as exc:
        state.log_lines = ["Stamp failed.", f"- {exc}"]
        state.status = "Failed."
        return
    finally:
        if tmp_payload_dir is not None:
            try:
                shutil.rmtree(tmp_payload_dir)
            except Exception:
                pass

    state.last_pack_dir = res.pack_dir
    state.last_out_dir = out_dir.resolve()
    state.verify_config.pack_path = str(res.pack_dir)
    pack_root = getattr(res, "pack_root_sha256", getattr(res, "root_sha256", ""))
    payload_root = getattr(res, "payload_root_sha256", "")
    zip_path = getattr(res, "zip_path", None)
    evidence_path = getattr(res, "evidence_bundle_path", None)
    evidence_sha = getattr(res, "evidence_bundle_sha256", None)
    state.log_lines = [
        "Stamp complete.",
        f"input: {_display_path(input_dir, max_len=44)}",
        f"out: {_display_path(out_dir, max_len=44)}",
        f"pack: {_display_path(res.pack_dir, max_len=44)}",
        f"pack_root_sha256: {pack_root}",
    ]
    if payload_root:
        state.log_lines.append(f"payload_root_sha256: {payload_root}")
    if exclude_relpaths:
        state.log_lines.append(f"excluded_artifacts: {len(exclude_relpaths)}")
    fp = res.receipt.get("derived_seed_fingerprint_sha256")
    if isinstance(fp, str) and fp:
        state.log_lines.append(f"derived_seed_fingerprint_sha256: {fp}")
    if seed_path_label:
        state.log_lines.append(f"Seed path: {seed_path_label}")
        if seed_path_label == "root-only seed" and state.authored_sources:
            state.log_lines.append("Warning: staged authored sources do not affect the root-only seed.")
    if mix_sources and pool_sha:
        state.log_lines.append(f"authored_sources_staged_count: {len(state.authored_sources)}")
        state.log_lines.append(f"authored_sources_eligible_count: {len(mixed_sources)}")
        state.log_lines.append(f"authored_sources_sha256: {pool_sha}")
    for w in audit_warnings:
        state.log_lines.append(f"warning: authored_sources audit: {w}")
    if audit_dir_path is not None:
        state.log_lines.append(f"authored_sources_dir: {_display_path(audit_dir_path, max_len=44)}")
    if zip_path is not None:
        state.log_lines.append(f"authored_pack.zip: {_display_path(zip_path, max_len=44)}")
    if evidence_path is not None:
        state.log_lines.append(f"evidence_bundle: {_display_path(evidence_path, max_len=44)}")
    if evidence_sha:
        state.log_lines.append(f"evidence_bundle_sha256: {evidence_sha}")
    if show_seed and res.seed_master is not None:
        _show_seed_reveal(state, res.seed_master)
    state.status = "Done."


def _run_stamp_from_config(state: AppState, stdscr) -> None:
    cfg = state.stamp_config
    if cfg.input_mode == "sources":
        input_s = "@sources"
    else:
        input_s = _effective_stamp_input(state)
        if not input_s:
            state.status = "Set an input folder or switch to Authored Sources before stamping."
            return
    out_s = _effective_stamp_output(state)
    _run_stamp_plan(
        state,
        stdscr,
        input_s=input_s,
        out_s=out_s,
        pack_id_s=cfg.pack_id,
        notes_s=cfg.notes,
        created_at_s=cfg.created_at_utc,
        include_hidden=cfg.include_hidden,
        exclude_picker=cfg.exclude_picker and cfg.input_mode != "sources",
        zip_pack=cfg.zip_pack,
        derive_seed=cfg.derive_seed,
        mix_sources=cfg.mix_sources,
        write_seed=cfg.write_seed if cfg.derive_seed else False,
        show_seed=cfg.show_seed if cfg.derive_seed else False,
        write_sources=cfg.write_sources if cfg.derive_seed else False,
        evidence_bundle=cfg.evidence_bundle,
    )


def _action_stamp(state: AppState, stdscr) -> None:
    # In-curses prompt sequence (no dropping out of the UI).
    state.status = "Stamp: configure..."
    state.log_lines = []
    rows, cols = stdscr.getmaxyx()
    stdscr.move(rows - 1, 0)
    stdscr.clrtoeol()
    stdscr.refresh()

    cfg = state.stamp_config
    default_out = _effective_stamp_output(state)
    default_in = "Authored Sources" if cfg.input_mode == "sources" else (_effective_stamp_input(state) or ".")
    input_s = _prompt_str_curses(stdscr, "(Authored Pack) input folder or Authored Sources", default=default_in)
    if input_s is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    out_s = _prompt_str_curses(stdscr, "(Authored Pack) output folder", default=default_out)
    if out_s is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    pack_id_s = _prompt_str_curses(stdscr, "(Authored Pack) label (optional)", default=cfg.pack_id)
    if pack_id_s is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    notes_s = _prompt_str_curses(stdscr, "(Authored Pack) note (optional)", default=cfg.notes)
    if notes_s is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    created_at_s = _prompt_str_curses(stdscr, "(Authored Pack) timestamp UTC (optional)", default=cfg.created_at_utc)
    if created_at_s is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    include_hidden = _prompt_bool_curses(stdscr, "(Authored Pack) include hidden files", default=cfg.include_hidden)
    if include_hidden is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    exclude_picker = False
    input_choice = _normalize_single_path_input(input_s.strip(), allow_sources=True)
    if input_choice != "@sources":
        exclude_picker = _prompt_bool_curses(stdscr, "(Authored Pack) pick files to exclude before stamp", default=cfg.exclude_picker)
        if exclude_picker is None:
            state.status = "Ready."
            state.log_lines = ["Stamp cancelled."]
            return
    zip_pack = _prompt_bool_curses(stdscr, "(Authored Pack) write authored_pack.zip", default=cfg.zip_pack)
    if zip_pack is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    derive_seed = _prompt_bool_curses(stdscr, "(Authored Pack) derive seed", default=cfg.derive_seed)
    if derive_seed is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    mix_sources = False
    if derive_seed and state.authored_sources:
        mix_sources = _prompt_bool_curses(stdscr, "(Authored Pack) use collected sources in seed", default=cfg.mix_sources)
        if mix_sources is None:
            state.status = "Ready."
            state.log_lines = ["Stamp cancelled."]
            return
    write_seed = _prompt_bool_curses(stdscr, "(Authored Pack) write seed files (chmod 600)", default=cfg.write_seed) if derive_seed else False
    if derive_seed and write_seed is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    show_seed = _prompt_bool_curses(stdscr, "(Authored Pack) show seed in UI", default=cfg.show_seed) if derive_seed else False
    if derive_seed and show_seed is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    write_sources_default = cfg.write_sources or bool(mix_sources)
    write_sources = (
        _prompt_bool_curses(
            stdscr,
            "(Authored Pack) save source record in pack",
            default=write_sources_default,
        )
        if derive_seed
        else False
    )
    if derive_seed and write_sources is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return
    evidence_default = bool(derive_seed)
    evidence_bundle = _prompt_bool_curses(
        stdscr,
        "(Authored Pack) write evidence bundle zip",
        default=cfg.evidence_bundle or evidence_default,
    )
    if evidence_bundle is None:
        state.status = "Ready."
        state.log_lines = ["Stamp cancelled."]
        return

    cfg.input_mode = "sources" if input_choice == "@sources" else "folder"
    cfg.input_path = "" if cfg.input_mode == "sources" else input_choice
    cfg.out_path = out_s.strip() or "./out"
    cfg.pack_id = pack_id_s.strip()
    cfg.notes = notes_s.strip()
    cfg.created_at_utc = created_at_s.strip()
    cfg.include_hidden = bool(include_hidden)
    cfg.exclude_picker = bool(exclude_picker)
    cfg.zip_pack = bool(zip_pack)
    cfg.derive_seed = bool(derive_seed)
    cfg.mix_sources = bool(mix_sources)
    cfg.write_seed = bool(write_seed) if derive_seed else False
    cfg.show_seed = bool(show_seed) if derive_seed else False
    cfg.write_sources = bool(write_sources) if derive_seed else False
    cfg.evidence_bundle = bool(evidence_bundle)

    _run_stamp_plan(
        state,
        stdscr,
        input_s=input_s,
        out_s=out_s,
        pack_id_s=pack_id_s,
        notes_s=notes_s,
        created_at_s=created_at_s,
        include_hidden=bool(include_hidden),
        exclude_picker=bool(exclude_picker),
        zip_pack=bool(zip_pack),
        derive_seed=bool(derive_seed),
        mix_sources=bool(mix_sources),
        write_seed=bool(write_seed) if derive_seed else False,
        show_seed=bool(show_seed) if derive_seed else False,
        write_sources=bool(write_sources) if derive_seed else False,
        evidence_bundle=bool(evidence_bundle),
    )


def _run_verify_plan(state: AppState, stdscr, *, pack_s: str, allow_large_manifest: bool) -> None:
    state.status = "Verify: review..."
    state.log_lines = []
    pack = Path(_normalize_single_path_input(pack_s)).expanduser()
    auto_selected = False
    if not pack.exists():
        state.log_lines = ["Verify failed.", f"- pack path not found: {_display_path(pack, max_len=44)}"]
        state.status = "Failed."
        return
    if pack.is_dir() and not (pack / "manifest.json").is_file():
        try:
            candidates = [p for p in pack.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
        except Exception:
            candidates = []
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            picked = candidates[0]
            auto_selected = True
            state.log_lines = [f"Auto-selected recent pack: {_display_path(picked, max_len=44)}"]
            pack = picked
    try:
        cap = (32 * 1024 * 1024) if allow_large_manifest else int(DEFAULT_MAX_MANIFEST_BYTES)
        state.status = "Verifying..."
        draw(stdscr, state)
        res = verify_pack(pack, max_manifest_bytes=int(cap))
    except Exception as exc:
        state.log_lines = ["Verify failed.", f"- {exc}"]
        state.status = "Failed."
        return
    remember_pack = auto_selected or (pack.is_dir() and (pack / "manifest.json").is_file()) or (
        pack.is_file() and pack.suffix.lower() == ".zip"
    )
    if remember_pack:
        state.verify_config.pack_path = str(pack)
    state.verify_config.allow_large_manifest = bool(allow_large_manifest)
    if res.ok and pack.is_dir():
        state.last_pack_dir = pack
    if res.ok:
        state.log_lines = [
            "Verify ok.",
            f"verified_path: {_display_path(pack, max_len=44)}",
            f"pack_root_sha256: {res.root_sha256}",
            f"artifact_count_verified: {res.file_count}",
            f"artifact_bytes_verified: {res.total_bytes}",
        ]
        if auto_selected:
            state.log_lines.insert(1, "used most recent pack in that folder")
        if res.payload_root_sha256:
            state.log_lines.insert(3 if auto_selected else 2, f"payload_root_sha256: {res.payload_root_sha256}")
        state.status = "Done."
    else:
        state.log_lines = ["Verify failed."] + [f"- {e}" for e in res.errors]
        if any("missing manifest.json" in e for e in res.errors):
            state.log_lines.append("")
            state.log_lines.append("Hint: verify expects a stamped pack dir (out/<root_sha256>/) or authored_pack.zip.")
        if any("manifest.json" in e and "file too large" in e for e in res.errors):
            state.log_lines.append("")
            state.log_lines.append("Hint: manifest.json is a file-list; very large packs need a larger cap (enable 'allow large manifest').")
        state.status = "Failed."


def _run_verify_from_config(state: AppState, stdscr) -> None:
    pack_s = _effective_verify_path(state)
    if not pack_s:
        if not _edit_verify_path(state, stdscr):
            return
        pack_s = _effective_verify_path(state)
        if not pack_s:
            state.status = "Ready."
            return
    _run_verify_plan(state, stdscr, pack_s=pack_s, allow_large_manifest=bool(state.verify_config.allow_large_manifest))


def _action_verify(state: AppState, stdscr) -> None:
    state.status = "Verify: configure..."
    state.log_lines = []
    default = _effective_verify_path(state) or "./out"
    pack_s = _prompt_str_curses(stdscr, "(Authored Pack) pack path (dir or .zip)", default=default)
    if pack_s is None:
        state.status = "Ready."
        state.log_lines = ["Verify cancelled."]
        return
    allow_large_manifest = _prompt_bool_curses(
        stdscr,
        "(Authored Pack) allow large manifest.json (32 MiB cap)",
        default=bool(state.verify_config.allow_large_manifest),
    )
    if allow_large_manifest is None:
        state.status = "Ready."
        state.log_lines = ["Verify cancelled."]
        return
    pack_s = _normalize_single_path_input(pack_s)
    state.verify_config.pack_path = pack_s
    state.verify_config.allow_large_manifest = bool(allow_large_manifest)
    _run_verify_plan(state, stdscr, pack_s=pack_s, allow_large_manifest=bool(allow_large_manifest))


def _ensure_noisy_profile_assets(state: AppState) -> None:
    if state.palette is None:
        state.palette = init_insane_palette()
    if state.godel_words or state.godel_phrase:
        return

    words: List[str] = _load_bundled_godel_words()
    src_s = (state.godel_source_arg or "").strip()
    if src_s:
        src = _resolve_godel_source(src_s)
        if src is None:
            state.godel_phrase = "NO GODEL SOURCE"
        else:
            if src.suffix.lower() == ".pdf":
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


def _set_experience_mode(state: AppState, *, noisy: bool) -> None:
    state.insane = bool(noisy)
    if state.insane:
        _ensure_noisy_profile_assets(state)


def _toggle_experience_mode(state: AppState) -> None:
    _set_experience_mode(state, noisy=(not state.insane))
    profile = _ui_profile_name(state)
    state.status = f"Experience: {profile} mode."
    if state.insane:
        state.log_lines = ["Noisy mode enabled: ceremony cues only. Pack and seed semantics are unchanged."]
    else:
        state.log_lines = ["Calm mode enabled: quiet guidance, no UI audio, same underlying tool behavior."]


def handle_key(stdscr, state: AppState, ch: int) -> bool:
    if state.viewer is not None:
        if ch in (27, ord("q"), ord("Q"), curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8, ord("b"), ord("B")):
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
    if ch == 27:
        if label == "Stamp" and state.stamp_panel_draft is not None:
            _close_stamp_panel(state)
            state.status = "Stamp review closed."
            return True
        if state.focus != "menu":
            state.focus = "menu"
        state.status = "Ready."
        return True
    if ch in (ord("q"), ord("Q"), curses.KEY_EXIT):
        return False

    if ch in (9, getattr(curses, "KEY_BTAB", -999)):
        if label == "Sources":
            if state.focus == "menu" and state.authored_sources:
                state.focus = "entropy"
            else:
                state.focus = "menu"
        else:
            state.focus = "menu"
        return True

    if ch in (ord("m"), ord("M")):
        _toggle_experience_mode(state)
        return True

    if label == "Start":
        if ch in (ord("s"), ord("S")):
            _set_current_lane(state, "authored")
            state.selected = state.menu.index("Sources")
            _close_stamp_panel(state)
            state.focus = "menu"
            state.status = "Ready."
            return True
        if ch in (ord("v"), ord("V")):
            state.selected = state.menu.index("Verify")
            _close_stamp_panel(state)
            state.focus = "menu"
            state.status = "Ready."
            return True
        if ch in (curses.KEY_ENTER, 10, 13):
            if state.insane:
                _start_ui_select_sfx_best_effort()
            _set_current_lane(state, "folder")
            state.stamp_config.input_mode = "folder"
            if _edit_stamp_input(state, stdscr, state.stamp_config, allow_sources=False):
                state.selected = state.menu.index("Stamp")
                state.focus = "menu"
                state.status = "Folder chosen. Review and stamp when ready."
            return True

    if label == "Sources":
        _set_current_lane(state, "authored")
        if ch in (curses.KEY_UP, ord("k")) and state.focus == "entropy" and state.authored_sources:
            state.entropy_selected = max(0, state.entropy_selected - 1)
            state.status = "Ready."
            return True
        if ch in (curses.KEY_DOWN, ord("j")) and state.focus == "entropy" and state.authored_sources:
            state.entropy_selected = min(max(0, len(state.authored_sources) - 1), state.entropy_selected + 1)
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
        if ch in (ord("p"), ord("P")):
            _action_sources_import_paths(state, stdscr)
            return True
        if ch in (ord("d"), ord("D")):
            _action_entropy_delete_selected(state)
            return True
        if ch in (ord("c"), ord("C")):
            _action_entropy_clear(state)
            return True
        if ch in (curses.KEY_ENTER, 10, 13) and state.focus == "entropy" and state.authored_sources:
            if state.insane:
                _start_ui_select_sfx_best_effort()
            _action_entropy_preview(state)
            return True
        if ch in (curses.KEY_ENTER, 10, 13) and state.focus == "menu":
            if state.authored_sources:
                state.selected = state.menu.index("Stamp")
                state.focus = "menu"
                state.status = "Authored Sources selected. Review and stamp when ready."
            else:
                state.status = "Compose from sources with A, T, Space, or P."
            return True

    if label == "Stamp":
        cfg = state.stamp_config
        if state.stamp_panel_draft is not None:
            rows = _stamp_panel_rows(state)
            if ch in (curses.KEY_UP, ord("k")):
                state.stamp_panel_selected = max(0, state.stamp_panel_selected - 1)
                state.status = "Stamp review."
                return True
            if ch in (curses.KEY_DOWN, ord("j")):
                state.stamp_panel_selected = min(max(0, len(rows) - 1), state.stamp_panel_selected + 1)
                state.status = "Stamp review."
                return True
            if ch == ord(" "):
                row = rows[max(0, min(int(state.stamp_panel_selected), len(rows) - 1))]
                if row.kind == "toggle":
                    _toggle_stamp_panel_value(state, row.key)
                    state.stamp_panel_selected = max(0, min(int(state.stamp_panel_selected), len(_stamp_panel_rows(state)) - 1))
                    return True
            if ch in (curses.KEY_ENTER, 10, 13):
                if state.insane:
                    _start_ui_select_sfx_best_effort()
                _activate_stamp_panel_row(state, stdscr)
                return True
            return True
        if ch in (ord("i"), ord("I")):
            _open_stamp_panel(state, "input")
            return True
        if ch in (ord("o"), ord("O")):
            _open_stamp_panel(state, "output")
            return True
        if ch in (ord("x"), ord("X")):
            _open_stamp_panel(state, "advanced", show_advanced=True)
            state.status = "More options shown."
            return True
        if ch in (ord("u"), ord("U")):
            if cfg.input_mode == "sources":
                _set_current_lane(state, "folder")
                cfg.input_mode = "folder"
            else:
                _set_current_lane(state, "authored")
                cfg.input_mode = "sources"
                cfg.input_path = ""
            state.log_lines = []
            state.status = f"Stamp input mode: {'Authored Sources' if cfg.input_mode == 'sources' else 'folder'}."
            return True
        if ch in (ord("d"), ord("D")):
            cfg.derive_seed = not cfg.derive_seed
            if not cfg.derive_seed:
                cfg.mix_sources = False
                cfg.write_seed = False
                cfg.show_seed = False
                cfg.write_sources = False
            state.log_lines = []
            state.status = f"Derive seed: {'on' if cfg.derive_seed else 'off'}."
            return True
        if ch in (ord("z"), ord("Z")):
            cfg.zip_pack = not cfg.zip_pack
            state.log_lines = []
            state.status = f"Zip pack: {'on' if cfg.zip_pack else 'off'}."
            return True
        if ch in (ord("e"), ord("E")):
            cfg.evidence_bundle = not cfg.evidence_bundle
            state.log_lines = []
            state.status = f"Evidence bundle: {'on' if cfg.evidence_bundle else 'off'}."
            return True
        if ch in (curses.KEY_ENTER, 10, 13):
            if state.insane:
                _start_ui_select_sfx_best_effort()
            _open_stamp_panel(state)
            return True

    if label == "Verify":
        if ch in (ord("p"), ord("P")):
            _edit_verify_path(state, stdscr)
            return True
        if ch in (ord("l"), ord("L")):
            state.verify_config.allow_large_manifest = not state.verify_config.allow_large_manifest
            state.log_lines = []
            state.status = f"Allow large manifest: {'yes' if state.verify_config.allow_large_manifest else 'no'}."
            return True
        if ch in (curses.KEY_ENTER, 10, 13):
            if state.insane:
                _start_ui_select_sfx_best_effort()
            _run_verify_from_config(state, stdscr)
            return True

    if label == "Help":
        if ch in (ord("r"), ord("R")):
            _open_help_doc(state, "readme")
            return True
        if ch in (curses.KEY_ENTER, 10, 13):
            if state.insane:
                _start_ui_select_sfx_best_effort()
            open_viewer(state, "Help", _help_summary_lines(state))
            return True

    if ch in (curses.KEY_UP, ord("k")):
        old = int(state.selected)
        state.selected = max(0, state.selected - 1)
        if state.selected != old and state.insane:
            _start_ui_move_sfx_best_effort()
        if state.menu[state.selected] != "Stamp":
            _close_stamp_panel(state)
        state.focus = "menu"
        state.status = "Ready."
        state.log_lines = []
        return True
    if ch in (curses.KEY_DOWN, ord("j")):
        old = int(state.selected)
        state.selected = min(len(state.menu) - 1, state.selected + 1)
        if state.selected != old and state.insane:
            _start_ui_move_sfx_best_effort()
        if state.menu[state.selected] != "Stamp":
            _close_stamp_panel(state)
        state.focus = "menu"
        state.status = "Ready."
        state.log_lines = []
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
    state = AppState(theme=theme, tick=0, godel_source_arg=godel_source)
    _set_experience_mode(state, noisy=bool(insane))

    stdscr.keypad(True)
    stdscr.nodelay(False)
    stdscr.timeout(50 if state.insane else 100)

    while True:
        state.tick += 1
        if state.reward_ticks > 0:
            state.reward_ticks -= 1
        if state.drop_flash_ticks > 0:
            state.drop_flash_ticks -= 1
        if state.interaction_flash_ticks > 0:
            state.interaction_flash_ticks -= 1
        if state.drop_last_msgs_ticks > 0:
            state.drop_last_msgs_ticks -= 1
            if state.drop_last_msgs_ticks == 0:
                state.drop_last_msgs = []
        _drain_drop_results(state)
        # If the terminal receives a fast pasted path burst, auto-commit it after a short idle gap.
        if state.drop_paste_buf and state.drop_paste_last_ns:
            if (time.monotonic_ns() - int(state.drop_paste_last_ns)) > 300_000_000:  # 300ms
                paths = _split_drop_payload(state.drop_paste_buf)
                state.drop_paste_buf = ""
                state.drop_paste_last_ns = 0
                if paths:
                    _queue_drop_paths(
                        state,
                        paths,
                        play_sfx=bool(state.insane),
                        apply_mode=_current_drop_apply_mode(state),
                        max_apply=7,
                    )
        # Poll the filesystem landing zone occasionally (not every tick) to stay cheap.
        if state.tick % 4 == 0:
            _poll_drop_dir(state)
        draw(stdscr, state)
        try:
            stdscr.timeout(50 if state.insane else 100)
        except curses.error:
            pass
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
    p = argparse.ArgumentParser(
        prog="authored-pack-tui",
        description="Calm-first TUI for Authored Pack. Default is Calm Mode; Noisy Mode keeps the ceremony cues without changing pack semantics.",
    )
    p.add_argument("--mode", choices=("calm", "noisy"), default="calm", help="Start in calm or noisy mode (default: calm)")
    p.add_argument("--noisy", action="store_true", help="Start in Noisy Mode")
    p.add_argument("--insane", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--godel-source",
        default=None,
        help="Optional text/markdown path for header words in Noisy Mode. PDFs are ignored; bundled words are used.",
    )
    ns = p.parse_args(list(argv) if argv is not None else None)
    start_noisy = bool(ns.noisy or ns.insane or ns.mode == "noisy")

    # The curses TUI must run in a real terminal/pty. When launched from an IDE
    # or a non-interactive environment, curses will crash with setupterm errors.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("authored-pack-tui: error: no TTY detected (curses requires an interactive terminal).", file=sys.stderr)
        print(
            "hint: run from a real terminal, or use the headless CLI: "
            "`python3 -m authored_pack stamp --input <dir> --out ./out`",
            file=sys.stderr,
        )
        return 2

    try:
        curses.wrapper(lambda stdscr: run_tui(stdscr, insane=start_noisy, godel_source=ns.godel_source))
    except curses.error as exc:
        # Keep failure mode readable (no traceback) for common terminal issues.
        msg = str(exc).strip() or exc.__class__.__name__
        print(f"authored-pack-tui: error: {msg}", file=sys.stderr)
        if "setupterm" in msg:
            print("hint: ensure `TERM` is set (e.g. xterm-256color) and run inside a real terminal.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
