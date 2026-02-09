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
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

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
    menu: List[str] = field(
        default_factory=lambda: [
            "Entropy Sources",
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
    Load words from either text/markdown or PDF.

    For PDFs we prefer `pdftotext` (if available) to extract readable words. If that fails,
    fall back to scanning the raw bytes for Latin-ish tokens (may be noisy).
    """
    suffix = path.suffix.lower()
    if suffix != ".pdf":
        return _load_wordlist_from_text_file(path, max_bytes=max_bytes)

    # Cache: avoid re-OCR/parsing every run.
    # Key is sha256 of the PDF bytes (small enough here; also robust against renames).
    cache_dir = Path(tempfile.gettempdir()) / "eps_godel_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        pdf_bytes = path.read_bytes()
    except Exception:
        pdf_bytes = b""
    cache_key = hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes else ""
    cache_txt = cache_dir / f"{cache_key}.txt" if cache_key else None
    # Versioned words cache so we can tighten filters without getting stuck on old noisy caches.
    cache_words = cache_dir / f"{cache_key}.words.v2.txt" if cache_key else None
    if cache_words is not None and cache_words.is_file():
        try:
            cached = [ln.strip() for ln in cache_words.read_text(encoding="utf-8", errors="ignore").splitlines()]
            cached = [w for w in cached if w]
            if len(cached) >= 10:
                return cached
        except Exception:
            pass
    if cache_txt is not None and cache_txt.is_file():
        words = _load_wordlist_from_text_file(cache_txt, max_bytes=max_bytes)
        words = _filter_words_en_de(words)
        if len(words) >= 10:
            if cache_words is not None:
                try:
                    cache_words.write_text("\n".join(words[:5000]) + "\n", encoding="utf-8")
                except Exception:
                    pass
            return words

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        fd, out_path_s = tempfile.mkstemp(prefix="eps_godel_", suffix=".txt")
        try:
            try:
                # Close the fd; pdftotext will write by path.
                import os as _os

                _os.close(fd)
            except Exception:
                pass
            out_path = Path(out_path_s)
            # Extract first 100 pages max (user asked "100 pages is fine").
            proc = subprocess.run(
                [pdftotext, "-f", "1", "-l", "100", "-enc", "UTF-8", str(path), str(out_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=60,
            )
            if proc.returncode == 0 and out_path.is_file():
                words = _load_wordlist_from_text_file(out_path, max_bytes=max_bytes)
                # Some PDFs have sparse text layers; we only need a small bank of usable tokens.
                words = _filter_words_en_de(words)
                if len(words) >= 10:
                    if cache_txt is not None:
                        try:
                            cache_txt.write_text(out_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
                        except Exception:
                            pass
                    if cache_words is not None and words:
                        try:
                            cache_words.write_text("\n".join(words[:5000]) + "\n", encoding="utf-8")
                        except Exception:
                            pass
                    return words
        except Exception:
            pass
        finally:
            try:
                Path(out_path_s).unlink(missing_ok=True)
            except Exception:
                pass

    # If the PDF has no text layer (common for scans), fall back to OCR.
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    magick = shutil.which("magick")
    if pdftoppm and tesseract:
        try:
            langs = "eng"
            try:
                out = subprocess.run([tesseract, "--list-langs"], capture_output=True, text=True, timeout=10, check=False)
                available = set((out.stdout or "").split())
                if "deu" in available and "eng" in available:
                    langs = "deu+eng"
            except Exception:
                pass

            with tempfile.TemporaryDirectory(prefix="eps_godel_ocr_") as td:
                out_prefix = str(Path(td) / "page")
                # Heavier first-run OCR is ok since we cache results.
                pages = 12
                dpi = 300
                subprocess.run(
                    [pdftoppm, "-f", "1", "-l", str(pages), "-r", str(dpi), "-png", str(path), out_prefix],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=90,
                )
                words: List[str] = []
                ocr_text_parts: List[str] = []
                for img in sorted(Path(td).glob("page-*.png")):
                    try:
                        img_for_ocr = img
                        # Optional preprocessing: improves OCR on scans (grayscale + normalize + threshold).
                        if magick:
                            pre = img.with_name(img.stem + ".pre.png")
                            subprocess.run(
                                [
                                    magick,
                                    str(img),
                                    "-colorspace",
                                    "Gray",
                                    "-auto-level",
                                    "-contrast-stretch",
                                    "0.5%x0.5%",
                                    "-sharpen",
                                    "0x1",
                                    "-threshold",
                                    "60%",
                                    str(pre),
                                ],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                                timeout=20,
                            )
                            if pre.is_file():
                                img_for_ocr = pre
                        proc = subprocess.run(
                            [tesseract, str(img_for_ocr), "stdout", "-l", langs, "--oem", "1", "--psm", "6"],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=45,
                        )
                        if proc.returncode != 0:
                            continue
                        ocr_text = proc.stdout or ""
                        if ocr_text:
                            ocr_text_parts.append(ocr_text)
                        chunk_words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", ocr_text)
                        words.extend(
                            [
                                w
                                for w in chunk_words
                                if 3 <= len(w) <= 22 and any(c in "aeiouyäöüAEIOUYÄÖÜ" for c in w)
                            ]
                        )
                        if len(words) >= 800:
                            break
                    except Exception:
                        continue
                if cache_txt is not None and ocr_text_parts:
                    try:
                        cache_txt.write_text("\n".join(ocr_text_parts), encoding="utf-8")
                    except Exception:
                        pass
                words = _filter_words_en_de(words)
                if cache_words is not None and words:
                    try:
                        cache_words.write_text("\n".join(words[:5000]) + "\n", encoding="utf-8")
                    except Exception:
                        pass
                if len(words) >= 10:
                    return words
        except Exception:
            pass

    # Fallback: brute scan PDF bytes (often low quality, but better than nothing).
    data = pdf_bytes
    if not data:
        return []
    if len(data) > int(max_bytes):
        data = data[: int(max_bytes)]
    text = data.decode("latin-1", errors="ignore")
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", text)
    return [w for w in words if 3 <= len(w) <= 22 and any(c in "aeiouyäöüAEIOUYÄÖÜ" for c in w)]


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
    # Glitch stripes: horizontal bands plus shifting vertical segments.
    bg = state.palette.bg
    if not bg:
        return

    seg = 26 if cols >= 160 else (22 if cols >= 140 else (18 if cols >= 120 else 12))
    wobble = 11 + ((state.tick // 11) % 29)  # longer loop
    direction = 1 if ((state.tick // 60) % 2 == 0) else -1
    ch_bank = [" ", "░", "▒", "▓"]

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
            safe_addstr(stdscr, y, x, (ch * run), bg[idx])
            x += run

        # Occasional tear bars.
        if (state.tick + y) % 31 == 0 and cols >= 16:
            tear_w = min(cols, 12 + ((row_seed >> 3) % 50))
            tear_attr = bg[(band + 7) % len(bg)]
            safe_addstr(stdscr, y, 0, ("▓" * tear_w), tear_attr)
        # Sparkle noise: a few high-contrast pixels that "crawl".
        if (row_seed % 7) == 0 and cols >= 6:
            sx = int((row_seed >> 9) % max(1, cols - 1))
            ch = "█" if (row_seed & 1) else "▒"
            safe_addstr(stdscr, y, sx, ch, bg[(band + 3) % len(bg)] | curses.A_BOLD)
        # Vertical scanlines: small high-frequency jitter overlay.
        if cols >= 40 and (row_seed & 0x1) == 0:
            step = 3 if cols >= 120 else 4
            for sx in range((row_seed >> 5) % step, cols, step):
                attr = bg[(band + (sx // step) + ((row_seed >> 11) & 0x7)) % len(bg)]
                safe_addstr(stdscr, y, sx, " ", attr | (curses.A_BOLD if (row_seed >> (sx % 9)) & 1 else 0))


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


def _entropy_sources_preview(state: AppState, *, width: int, height: int) -> List[str]:
    """
    Right-pane content for the Entropy Sources screen.
    """
    min_n = int(state.entropy_min_sources)
    n = len(state.entropy_sources)
    pool = _entropy_pool_sha256(state.entropy_sources)[:16] if state.entropy_sources else "none"
    header = f"ENTROPY SOURCES // {n}/{min_n} required for LOCKDOWN seed  pool={pool}"
    lines: List[str] = [header, ""]
    lines.append("Keys: a=add photos  t=add text  Space=tap entropy  d=delete  c=clear  Enter=preview")
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
    if label == "Entropy Sources":
        preview = _entropy_sources_preview(state, width=right_w, height=body_h)
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
        raw = stdscr.getstr(y, min(len(prompt), max(0, cols - 1)), min(max_len, max(1, cols - len(prompt) - 1)))
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
    state.status = "Done."
    state.log_lines = [f"Added tap source: events={count} sha={digest[:10]}."]


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
    state.status = "Stamp: configure..."
    state.log_lines = []
    rows, cols = stdscr.getmaxyx()
    stdscr.move(rows - 1, 0)
    stdscr.clrtoeol()
    stdscr.refresh()

    default_out = str(state.last_out_dir) if state.last_out_dir is not None else "./out"
    input_s = _prompt_str_curses(stdscr, "(EPS) input dir (or @sources)", default=".")
    out_s = _prompt_str_curses(stdscr, "(EPS) out dir", default=default_out)
    pack_id_s = _prompt_str_curses(stdscr, "(EPS) pack_id (optional)", default="")
    notes_s = _prompt_str_curses(stdscr, "(EPS) notes (optional)", default="")
    created_at_s = _prompt_str_curses(stdscr, "(EPS) created_at_utc (optional)", default="")
    include_hidden = _prompt_bool_curses(stdscr, "(EPS) include hidden files", default=False)
    zip_pack = _prompt_bool_curses(stdscr, "(EPS) write entropy_pack.zip", default=True)
    derive_seed = _prompt_bool_curses(stdscr, "(EPS) derive seed_master (LOCKDOWN)", default=bool(state.insane))
    mix_sources = False
    pool_sha = None
    if derive_seed and state.entropy_sources:
        mix_sources = _prompt_bool_curses(stdscr, "(EPS) mix staged entropy sources into seed", default=True)
        if mix_sources:
            if len(state.entropy_sources) < int(state.entropy_min_sources):
                state.status = "Failed."
                state.log_lines = [
                    "Stamp blocked: not enough entropy sources.",
                    f"Need {state.entropy_min_sources}, have {len(state.entropy_sources)}.",
                    "Go to Entropy Sources and add more (photos/text/tap).",
                ]
                return
            pool_sha = _entropy_pool_sha256(state.entropy_sources)
    write_seed = _prompt_bool_curses(stdscr, "(EPS) write seed files (chmod 600)", default=False) if derive_seed else False
    show_seed = _prompt_bool_curses(stdscr, "(EPS) show seed in UI", default=False) if derive_seed else False
    write_sources = _prompt_bool_curses(stdscr, "(EPS) write entropy_sources into pack (excluded from zip)", default=False) if derive_seed else False

    tmp_payload_dir: Optional[Path] = None
    input_dir: Path
    if input_s.strip() == "@sources":
        if not state.entropy_sources:
            state.status = "Failed."
            state.log_lines = ["@sources selected, but no entropy sources are staged."]
            return
        tmp_payload_dir = _build_sources_payload_dir(state.entropy_sources)
        input_dir = tmp_payload_dir
    else:
        input_dir = Path(input_s).expanduser()
    out_dir = Path(out_s).expanduser()
    pack_id = pack_id_s.strip() or None
    notes = notes_s.strip() or None
    created_at = created_at_s.strip() or None

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
            entropy_sources_sha256=pool_sha if mix_sources else None,
            write_seed_files=write_seed,
            print_seed=False,  # never print to stdout from TUI
        )
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
        res = verify_pack(pack)
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
        state.status = "Failed."


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

    label = state.menu[state.selected] if 0 <= state.selected < len(state.menu) else ""

    # Entropy Sources mode has its own navigation/actions.
    if label == "Entropy Sources":
        if ch in (curses.KEY_UP, ord("k")):
            state.entropy_selected = max(0, state.entropy_selected - 1)
            state.status = "Ready."
            return True
        if ch in (curses.KEY_DOWN, ord("j")):
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
        if ch in (curses.KEY_ENTER, 10, 13):
            _action_entropy_preview(state)
            return True

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
        if label == "Stamp Pack":
            _action_stamp(state, stdscr)
            return True
        if label == "Verify Pack":
            _action_verify(state, stdscr)
            return True
        if label == "Entropy Sources":
            # Enter is handled above for preview; keep as a no-op here.
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
                    state.godel_phrase = "GODEL OCR TOO NOISY" if src.suffix.lower() == ".pdf" else "EMPTY GODEL TEXT"

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
