from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Optional, Tuple


_CHUNK_SIZE = 1024 * 1024


def _is_regular_file(mode: int) -> bool:
    return stat.S_ISREG(mode)


def _open_flags() -> int:
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= int(nofollow)
    return flags


def _same_identity(a: os.stat_result, b: os.stat_result) -> bool:
    return (
        int(a.st_dev) == int(b.st_dev)
        and int(a.st_ino) == int(b.st_ino)
        and int(a.st_mode) == int(b.st_mode)
    )


def open_trusted_binary(path: Path) -> BinaryIO:
    path = Path(path)
    before: Optional[os.stat_result] = None
    if not getattr(os, "O_NOFOLLOW", 0):
        before = os.lstat(path)
        if not _is_regular_file(int(before.st_mode)):
            raise ValueError(f"refusing to read non-regular file: {path}")

    try:
        fd = os.open(path, _open_flags())
    except OSError as exc:
        raise ValueError(f"failed to open trusted file: {path}: {exc}") from exc

    try:
        after = os.fstat(fd)
        if not _is_regular_file(int(after.st_mode)):
            raise ValueError(f"refusing to read non-regular file: {path}")
        if before is not None and not _same_identity(before, after):
            raise ValueError(f"trusted file changed during open: {path}")
        return os.fdopen(fd, "rb")
    except Exception:
        os.close(fd)
        raise


@contextmanager
def trusted_binary_reader(path: Path) -> Iterator[BinaryIO]:
    handle = open_trusted_binary(path)
    try:
        yield handle
    finally:
        handle.close()


def read_trusted_bytes_limited(path: Path, *, max_bytes: int) -> bytes:
    with trusted_binary_reader(path) as handle:
        data = handle.read(int(max_bytes) + 1)
    if len(data) > int(max_bytes):
        raise ValueError(f"file too large ({len(data)} > {max_bytes})")
    return data


def trusted_sha256_hex(path: Path, *, max_bytes: Optional[int] = None) -> Tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with trusted_binary_reader(path) as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            n += len(chunk)
            if max_bytes is not None and n > int(max_bytes):
                raise ValueError(f"stream exceeded max_bytes ({n} > {max_bytes})")
            h.update(chunk)
    return h.hexdigest(), n


def hash_trusted_file(path: Path, *, max_bytes: Optional[int] = None) -> Tuple[str, int]:
    return trusted_sha256_hex(path, max_bytes=max_bytes)


def trusted_copy_with_sha256(src: Path, dst: Path, *, max_bytes: Optional[int] = None) -> Tuple[str, int]:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    n = 0
    with trusted_binary_reader(src) as handle, dst.open("wb") as out:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            n += len(chunk)
            if max_bytes is not None and n > int(max_bytes):
                raise ValueError(f"stream exceeded max_bytes ({n} > {max_bytes})")
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest(), n
