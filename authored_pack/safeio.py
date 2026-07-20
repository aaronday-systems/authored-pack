from __future__ import annotations

import hashlib
import os
import stat
import tempfile
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
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if nonblock:
        flags |= int(nonblock)
    return flags


def _same_identity(a: os.stat_result, b: os.stat_result) -> bool:
    return (
        int(a.st_dev) == int(b.st_dev)
        and int(a.st_ino) == int(b.st_ino)
        and int(a.st_mode) == int(b.st_mode)
        and int(a.st_size) == int(b.st_size)
        and int(a.st_mtime_ns) == int(b.st_mtime_ns)
    )


def open_trusted_binary(path: Path) -> BinaryIO:
    path = Path(path)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"failed to inspect trusted file: {path}: {exc}") from exc
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
        if not _same_identity(before, after):
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
        trusted_size = int(os.fstat(handle.fileno()).st_size)
        if trusted_size > int(max_bytes):
            raise ValueError(f"file too large ({trusted_size} > {max_bytes})")
        data = handle.read(int(max_bytes) + 1)
    if len(data) > int(max_bytes):
        raise ValueError(f"file too large ({len(data)} > {max_bytes})")
    return data


def trusted_sha256_hex(path: Path, *, max_bytes: Optional[int] = None) -> Tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with trusted_binary_reader(path) as handle:
        trusted_size = int(os.fstat(handle.fileno()).st_size)
        if max_bytes is not None and trusted_size > int(max_bytes):
            raise ValueError(f"stream exceeded max_bytes ({trusted_size} > {max_bytes})")
        while True:
            read_size = _CHUNK_SIZE if max_bytes is None else min(_CHUNK_SIZE, int(max_bytes) - n + 1)
            chunk = handle.read(read_size)
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
    fd, tmp_name = tempfile.mkstemp(prefix=".ap-copy-", dir=str(dst.parent))
    tmp_path = Path(tmp_name)
    try:
        try:
            os.fchmod(fd, 0o644)
        except OSError:
            pass
        with trusted_binary_reader(src) as handle, os.fdopen(fd, "wb") as out:
            trusted_size = int(os.fstat(handle.fileno()).st_size)
            if max_bytes is not None and trusted_size > int(max_bytes):
                raise ValueError(f"stream exceeded max_bytes ({trusted_size} > {max_bytes})")
            while True:
                read_size = _CHUNK_SIZE if max_bytes is None else min(_CHUNK_SIZE, int(max_bytes) - n + 1)
                chunk = handle.read(read_size)
                if not chunk:
                    break
                n += len(chunk)
                if max_bytes is not None and n > int(max_bytes):
                    raise ValueError(f"stream exceeded max_bytes ({n} > {max_bytes})")
                h.update(chunk)
                out.write(chunk)
        os.replace(tmp_path, dst)
        return h.hexdigest(), n
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
