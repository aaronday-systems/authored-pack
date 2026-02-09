#!/usr/bin/env python3
"""
Minimal curses TUI for Entropy Pack Stamper (EPS).

Baseline: copy the Control Plane interaction posture:
- 3-line header band (identity + status + divider)
- left menu + right preview/log
- action execution runs outside curses (prompt -> run -> return)

ASCII-first: no box-drawing or emoji. Color is optional.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

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


@dataclass
class ViewerState:
    title: str
    lines: List[str]
    top: int = 0


@dataclass
class AppState:
    theme: Theme
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
    risk = "INFO" if not state.log_lines else "OK"
    action = "none"
    status_line = f"MODE: {mode}  RISK: {risk}  ACTION: {action}"
    safe_addstr(stdscr, 1, 0, status_line[:cols].ljust(cols), state.theme.normal)

    divider = _divider_for_width(cols)
    safe_addstr(stdscr, 2, 0, divider[:cols].ljust(cols), state.theme.normal)


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

    _draw_header(stdscr, state, cols)
    body_top = 3

    if state.viewer is not None:
        _draw_viewer(stdscr, state, body_top, cols, rows)
        _draw_footer(stdscr, state, rows, cols)
        stdscr.refresh()
        return

    # Menu on the left, fixed width.
    left_w = min(28, max(18, cols // 3))
    body_h = rows - body_top - 1

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
        res = verify_pack(pack)
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


def run_tui(stdscr) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    try:
        curses.set_escdelay(25)
    except curses.error:
        pass

    theme = init_theme()
    state = AppState(theme=theme)

    stdscr.keypad(True)
    stdscr.nodelay(False)
    stdscr.timeout(100)

    while True:
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


def main() -> int:
    curses.wrapper(run_tui)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
