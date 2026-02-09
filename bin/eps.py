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
import curses
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

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
from eps.pack import stamp_pack, verify_pack


APP_NAME = "ENTROPY PACK STAMPER"
APP_VERSION = f"v{__version__}"

DIVIDER_WIDE = "-------+-------+-------+-------+-------+-------+-------+-------+-------+-------+"
DIVIDER_NARROW = "-------+-------+-------+-------+-------+-------+-------+----"


def safe_addstr(stdscr, y: int, x: int, s: str, attr: int = 0) -> None:
    try:
        stdscr.addstr(y, x, s, attr)
    except curses.error:
        return


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

    bg = [curses.color_pair(11), curses.color_pair(12), curses.color_pair(13), curses.color_pair(14), curses.color_pair(15)]
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
class AppState:
    theme: Theme
    insane: bool = False
    palette: Optional[InsanePalette] = None
    tick: int = 0
    godel_words: List[str] = field(default_factory=list)
    godel_phrase: str = ""
    godel_last_tick: int = 0
    menu: List[str] = field(
        default_factory=lambda: [
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
    # Keep only moderately-sized tokens so the header doesn't explode.
    out = [w for w in words if 2 <= len(w) <= 18]
    return out


def _load_wordlist_from_source(path: Path, *, max_bytes: int = 5_000_000) -> List[str]:
    """
    Load words from either text/markdown or PDF.

    For PDFs we prefer `pdftotext` (if available) to extract readable words. If that fails,
    fall back to scanning the raw bytes for Latin-ish tokens (may be noisy).
    """
    suffix = path.suffix.lower()
    if suffix != ".pdf":
        return _load_wordlist_from_text_file(path, max_bytes=max_bytes)

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        fd, out_path_s = tempfile.mkstemp(prefix="eps_godel_", suffix=".txt")
        try:
            os_handle = None
            try:
                os_handle = fd
            finally:
                try:
                    # Close the fd; pdftotext will write by path.
                    import os as _os

                    _os.close(fd)
                except Exception:
                    pass
            out_path = Path(out_path_s)
            # Extract first 100 pages max (user asked "100 pages is fine").
            proc = subprocess.run(
                [pdftotext, "-f", "1", "-l", "100", str(path), str(out_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if proc.returncode == 0 and out_path.is_file():
                return _load_wordlist_from_text_file(out_path, max_bytes=max_bytes)
        except Exception:
            pass
        finally:
            try:
                Path(out_path_s).unlink(missing_ok=True)
            except Exception:
                pass

    # Fallback: brute scan PDF bytes (often low quality, but better than nothing).
    try:
        data = path.read_bytes()
    except Exception:
        return []
    if len(data) > int(max_bytes):
        data = data[: int(max_bytes)]
    text = data.decode("latin-1", errors="ignore")
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", text)
    return [w for w in words if 2 <= len(w) <= 18]


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
    for y in range(rows):
        attr = _cycle(state.palette.bg, state.tick + y, speed=1, default=state.palette.text)
        safe_addstr(stdscr, y, 0, (" " * cols), attr)


def _draw_insane_header(stdscr, state: AppState, cols: int) -> None:
    if state.palette is None:
        return

    head_attr = _cycle(state.palette.header, state.tick, speed=1, default=state.palette.text)
    safe_addstr(stdscr, 0, 0, (" " * cols), head_attr)

    _update_godel_phrase(state)
    phase = int(time.monotonic() * 8) % 4
    fallback = ["NEON", "RAVE", "GLITCH", "HOT"][phase]
    tag = state.godel_phrase or fallback
    title = f" {tag} // {APP_NAME} {APP_VERSION} "
    safe_addstr(stdscr, 0, 0, title[:cols].ljust(cols), head_attr)

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
        attr = _cycle(state.palette.menu_hot, state.tick + idx, speed=2, default=state.palette.menu_dim) if selected else state.palette.menu_dim
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


def _draw_insane_right_pane(stdscr, state: AppState, top: int, left_w: int, cols: int, rows: int) -> None:
    if state.palette is None:
        return
    body_h = rows - top - 1
    right_x = left_w + 1
    right_w = max(0, cols - right_x)

    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""
    preview: List[str] = []
    if label == "Stamp Pack":
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

    if state.log_lines:
        preview = state.log_lines[-(body_h - 1) :]

    for i in range(body_h):
        y = top + i
        safe_addstr(stdscr, y, left_w, "║", state.palette.divider)

    for i in range(body_h):
        y = top + i
        line = preview[i] if i < len(preview) else ""
        attr = state.palette.text if i % 2 == 0 else _cycle(state.palette.bg, state.tick + i, speed=4, default=state.palette.text)
        safe_addstr(stdscr, y, right_x, line[:right_w].ljust(right_w), attr)


def _draw_footer(stdscr, state: AppState, rows: int, cols: int) -> None:
    legend = "Up/Down: move  Enter: select  q: quit  Esc: back"
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
        safe_addstr(stdscr, y, 0, text, state.theme.reverse if selected else state.theme.normal)


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
    if label == "Stamp Pack":
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
    if state.log_lines:
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


def _prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{label} [{suffix}]: ").strip().lower()
    if not raw:
        return bool(default)
    return raw in ("y", "yes", "true", "1")


def _action_stamp(state: AppState, stdscr) -> None:
    def run() -> None:
        print("(EPS) Stamp Pack")
        input_dir = Path(input("(EPS) input dir: ").strip() or ".").expanduser()
        out_dir = Path(input("(EPS) out dir: ").strip() or "./out").expanduser()
        pack_id = input("(EPS) pack_id (optional): ").strip() or None
        notes = input("(EPS) notes (optional): ").strip() or None
        created_at = input("(EPS) created_at_utc (optional, affects root): ").strip() or None
        include_hidden = _prompt_bool("(EPS) include hidden files", default=False)
        zip_pack = _prompt_bool("(EPS) write entropy_pack.zip", default=True)
        derive_seed = _prompt_bool("(EPS) derive seed_master", default=False)
        write_seed = _prompt_bool("(EPS) write seed files (chmod 600)", default=False) if derive_seed else False
        print_seed = _prompt_bool("(EPS) print seed to stdout", default=False) if derive_seed else False

        try:
            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                pack_id=pack_id,
                notes=notes,
                created_at_utc=created_at,
                include_hidden=include_hidden,
                zip_pack=zip_pack,
                derive_seed=derive_seed,
                write_seed_files=write_seed,
                print_seed=print_seed,
            )
        except Exception as exc:
            print("")
            print("stamp_failed")
            print(f"- {exc}")
            state.log_lines = ["Stamp failed.", f"- {exc}"]
            state.status = "Failed."
            return
        print("")
        print(f"pack_dir: {res.pack_dir}")
        print(f"entropy_root_sha256: {res.root_sha256}")
        fp = res.receipt.get("seed_fingerprint_sha256")
        if isinstance(fp, str) and fp:
            print(f"seed_fingerprint_sha256: {fp}")

        state.log_lines = [
            "Stamp complete.",
            f"pack_dir: {res.pack_dir}",
            f"entropy_root_sha256: {res.root_sha256}",
        ]
        state.status = "Done."

    _run_outside_curses(stdscr, run)


def _action_verify(state: AppState, stdscr) -> None:
    def run() -> None:
        print("(EPS) Verify Pack")
        pack = Path(input("(EPS) pack path (dir or .zip): ").strip() or ".").expanduser()
        try:
            res = verify_pack(pack)
        except Exception as exc:
            print("")
            print("verify_failed")
            print(f"- {exc}")
            state.log_lines = ["Verify failed.", f"- {exc}"]
            state.status = "Failed."
            return
        if res.ok:
            print("ok")
            print(f"entropy_root_sha256: {res.root_sha256}")
            print(f"artifact_count_verified: {res.file_count}")
            print(f"artifact_bytes_verified: {res.total_bytes}")
            state.log_lines = [
                "Verify ok.",
                f"entropy_root_sha256: {res.root_sha256}",
                f"artifact_count_verified: {res.file_count}",
                f"artifact_bytes_verified: {res.total_bytes}",
            ]
            state.status = "Done."
        else:
            print("verify_failed")
            for e in res.errors:
                print(f"- {e}")
            state.log_lines = ["Verify failed."] + [f"- {e}" for e in res.errors]
            state.status = "Failed."

    _run_outside_curses(stdscr, run)


def handle_key(stdscr, state: AppState, ch: int) -> bool:
    if state.viewer is not None:
        if ch in (27, ord("q"), ord("Q")):
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

    if ch in (ord("q"), ord("Q"), 27, curses.KEY_EXIT):
        label = state.menu[state.selected]
        if label == "Quit":
            return False
        # Allow quick quit regardless of selection.
        return False

    if ch in (curses.KEY_UP, ord("k")):
        state.selected = max(0, state.selected - 1)
        state.status = "Ready."
        state.log_lines = []
        return True
    if ch in (curses.KEY_DOWN, ord("j")):
        state.selected = min(len(state.menu) - 1, state.selected + 1)
        state.status = "Ready."
        state.log_lines = []
        return True

    if ch in (curses.KEY_ENTER, 10, 13):
        label = state.menu[state.selected]
        if label == "Stamp Pack":
            _action_stamp(state, stdscr)
            return True
        if label == "Verify Pack":
            _action_verify(state, stdscr)
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
        src_s = (godel_source or "").strip()
        if src_s:
            src = _resolve_godel_source(src_s)
            if src is None:
                # Make misconfiguration visible; don't silently fall back.
                state.godel_phrase = "NO GODEL SOURCE"
            else:
                words = _load_wordlist_from_source(src, max_bytes=5_000_000)
                state.godel_words = words
                if words:
                    _update_godel_phrase(state, min_interval_ticks=0)
                else:
                    state.godel_phrase = "EMPTY GODEL TEXT"

    stdscr.keypad(True)
    stdscr.nodelay(False)
    stdscr.timeout(50 if insane else 100)

    while True:
        state.tick += 1
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
    p.add_argument("--godel-source", default=None, help="Path to a text/markdown file to sample header words from (insane mode)")
    ns = p.parse_args(list(argv) if argv is not None else None)
    curses.wrapper(lambda stdscr: run_tui(stdscr, insane=bool(ns.insane), godel_source=ns.godel_source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
