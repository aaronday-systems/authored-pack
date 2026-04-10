from __future__ import annotations

import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .pack import StampResult, stamp_pack


def _paths_overlap(a: Path, b: Path) -> bool:
    ra = Path(a).expanduser().resolve()
    rb = Path(b).expanduser().resolve()
    return ra == rb or ra.is_relative_to(rb) or rb.is_relative_to(ra)


_DEFAULT_EXCLUDE_DIRS = {
    ".authored_pack_failed",
    ".authored_pack_stage",
    ".eps_used",
    "__MACOSX",
}

_DEFAULT_EXCLUDE_FILES = {
    ".DS_Store",
    "Thumbs.db",
}


@dataclass(frozen=True)
class ConsumedItem:
    src_path: Path
    staged_path: Path


@dataclass(frozen=True)
class BinStampResult:
    stamp: StampResult
    source_bin: Path
    consumed: Tuple[ConsumedItem, ...]
    bin_files_before: int
    bin_files_after: int


class BinRecoveryError(RuntimeError):
    pass


def _iter_source_bin_files(
    source_bin: Path,
    *,
    recursive: bool = True,
    include_hidden: bool = False,
    exclude_dirs: Optional[Sequence[str]] = None,
    exclude_files: Optional[Sequence[str]] = None,
) -> Iterable[Path]:
    source_bin = Path(source_bin)
    ex_dirs = set(_DEFAULT_EXCLUDE_DIRS)
    ex_files = set(_DEFAULT_EXCLUDE_FILES)
    if exclude_dirs:
        ex_dirs |= {str(x) for x in exclude_dirs}
    if exclude_files:
        ex_files |= {str(x) for x in exclude_files}

    it = source_bin.rglob("*") if recursive else source_bin.glob("*")
    for p in it:
        try:
            rel_parts = p.relative_to(source_bin).parts
        except Exception:
            rel_parts = ()
        if not include_hidden and any(str(part).startswith(".") for part in rel_parts):
            continue
        if any(part in ex_dirs for part in rel_parts):
            continue
        if p.name in ex_files:
            continue
        try:
            if p.is_file() and not p.is_symlink():
                yield p
        except OSError:
            continue


def _unique_stage_name(stage_dir: Path, base_name: str) -> Path:
    base = base_name.strip() or "entropy.bin"
    base = base.replace("/", "_")
    dst = stage_dir / base
    if not dst.exists():
        return dst
    stem = dst.stem or "entropy"
    suf = dst.suffix
    for i in range(1, 10_000):
        cand = stage_dir / f"{stem}_{i}{suf}"
        if not cand.exists():
            return cand
    # Last resort: random suffix.
    return stage_dir / f"{stem}_{secrets.token_hex(4)}{suf}"


def stamp_from_source_bin(
    *,
    source_bin: Path,
    out_dir: Path,
    count: int = 7,
    min_remaining: int = 50,
    allow_low_bin: bool = False,
    recursive: bool = True,
    include_hidden: bool = False,
    zip_pack: bool = True,
    derive_seed: bool = True,
    evidence_bundle: bool = True,
) -> BinStampResult:
    """
    One-shot "push button" mode:
    - Randomly select N files from source_bin
    - Move-consume them (subtractive)
    - Stamp them as the payload of a new Authored Pack under out_dir

    Low-watermark policy:
    - By default, refuse to run if remaining files after consumption would drop below min_remaining.
      Set allow_low_bin=True to proceed with a warning.
    """
    source_bin = Path(source_bin).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    if not source_bin.is_dir():
        raise ValueError(f"--source-bin must be a directory: {source_bin}")
    if _paths_overlap(source_bin, out_dir):
        raise ValueError("--source-bin and --out must not overlap")
    if count <= 0:
        raise ValueError(f"--count must be > 0, got: {count}")
    if min_remaining < 0:
        raise ValueError(f"--min-remaining must be >= 0, got: {min_remaining}")

    candidates = list(
        _iter_source_bin_files(
            source_bin,
            recursive=recursive,
            include_hidden=bool(include_hidden),
        )
    )
    before = len(candidates)
    if before < count:
        raise ValueError(f"source bin has {before} files, need at least {count}")
    after = before - int(count)
    if after < min_remaining and not allow_low_bin:
        raise ValueError(
            f"source bin low-watermark: would leave {after} files after consuming {count} (min_remaining={min_remaining}). "
            "Use --allow-low-bin to proceed anyway."
        )

    rng = secrets.SystemRandom()
    chosen = list(rng.sample(candidates, int(count)))
    chosen.sort(key=lambda p: str(p))  # stable order for staging names / UX

    out_dir.mkdir(parents=True, exist_ok=True)
    stage_root = source_bin / ".authored_pack_stage"
    stage_root.mkdir(parents=True, exist_ok=True)
    stage_dir = stage_root / f"bin_{int(time.time())}_{secrets.token_hex(4)}"
    stage_dir.mkdir(parents=True, exist_ok=False)

    consumed: List[ConsumedItem] = []
    cleanup_stage_dir = True
    try:
        # Move files into a staging input_dir. stamp_pack will copy them into payload/.
        # After success we delete stage_dir, leaving only the pack copies (destructive/subtractive).
        for i, src in enumerate(chosen, start=1):
            dst = _unique_stage_name(stage_dir, f"{i:02d}__{src.name}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            consumed.append(ConsumedItem(src_path=src, staged_path=dst))

        # stamp_pack already enforces input_dir contains at least 1 artifact.
        stamp = stamp_pack(
            input_dir=stage_dir,
            out_dir=out_dir,
            include_hidden=bool(include_hidden),
            zip_pack=bool(zip_pack),
            derive_seed=bool(derive_seed),
            authored_sources_sha256=None,  # sources are the payload in this mode
            evidence_bundle=bool(evidence_bundle),
            write_seed_files=False,
            print_seed=False,
        )
    except Exception as exc:
        # Recovery: put the staging tree back under a deterministic failure folder,
        # so a failed run does not silently destroy source material.
        cleanup_stage_dir = False
        failed_root = source_bin / ".authored_pack_failed" / f"{int(time.time())}_{secrets.token_hex(4)}"
        preserved_path: Path = failed_root
        try:
            failed_root.parent.mkdir(parents=True, exist_ok=True)
            # Preserve the whole staging directory with whatever remains unrecovered.
            shutil.move(str(stage_dir), str(failed_root))
            preserved_path = failed_root
        except Exception:
            try:
                nested_preserve = failed_root / stage_dir.name
                failed_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(stage_dir), str(nested_preserve))
                preserved_path = nested_preserve
            except Exception:
                preserved_path = stage_dir
        raise BinRecoveryError(
            f"consume-bin failed; preserved staged files at {preserved_path}: {exc}"
        ) from exc
    finally:
        if cleanup_stage_dir:
            try:
                shutil.rmtree(stage_dir)
            except Exception:
                pass

    # Post-run counts are best-effort (bin contents changed).
    try:
        after_count = len(list(_iter_source_bin_files(source_bin, recursive=recursive, include_hidden=bool(include_hidden))))
    except Exception:
        after_count = -1

    return BinStampResult(
        stamp=stamp,
        source_bin=source_bin,
        consumed=tuple(consumed),
        bin_files_before=before,
        bin_files_after=after_count,
    )
