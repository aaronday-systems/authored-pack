#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_CMD = [sys.executable, "-B", "bin/authored_pack.py"]
BASE_ENV = {
    "TERM": "xterm-256color",
    "PYTHONUNBUFFERED": "1",
}


def _set_winsize(fd: int, *, rows: int, cols: int) -> None:
    data = struct.pack("HHHH", int(rows), int(cols), 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, data)


def _read_available(fd: int, transcript: bytearray, *, timeout_s: float = 0.05) -> bool:
    ready, _, _ = select.select([fd], [], [], max(0.0, float(timeout_s)))
    if not ready:
        return False
    try:
        chunk = os.read(fd, 65536)
    except OSError:
        return False
    if not chunk:
        return False
    transcript.extend(chunk)
    return True


def _wait_for(
    fd: int,
    transcript: bytearray,
    proc: subprocess.Popen[bytes],
    marker: str,
    *,
    start: int,
    timeout_s: float = 5.0,
) -> None:
    needle = marker.encode("utf-8").lower()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if needle in bytes(transcript[start:]).lower():
            return
        if proc.poll() is not None:
            break
        _read_available(fd, transcript)
    tail = transcript[-4000:].decode("utf-8", errors="ignore")
    raise AssertionError(f"missing visible marker {marker!r}\n{tail}")


def _wait_for_terminal(fd: int, transcript: bytearray, proc: subprocess.Popen[bytes], *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while proc.poll() is None and time.monotonic() < deadline:
        _read_available(fd, transcript)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1.0)
        raise AssertionError("timed out waiting for terminal TUI state")
    while _read_available(fd, transcript, timeout_s=0.01):
        pass


def _run_case(
    name: str,
    *,
    argv: list[str],
    actions: list[tuple[str, bytes]],
    required_substrings: list[str] | None = None,
    rows: int = 24,
    cols: int = 80,
) -> None:
    master_fd, slave_fd = pty.openpty()
    _set_winsize(slave_fd, rows=rows, cols=cols)
    env = os.environ.copy()
    env.update(BASE_ENV)
    env["LINES"] = str(rows)
    env["COLUMNS"] = str(cols)

    proc = subprocess.Popen(
        argv,
        cwd=ROOT,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
    )
    os.close(slave_fd)

    transcript = bytearray()
    try:
        marker_start = 0
        for marker, payload in actions:
            _wait_for(master_fd, transcript, proc, marker, start=marker_start)
            os.write(master_fd, payload)
            marker_start = len(transcript)

        _wait_for_terminal(master_fd, transcript, proc)
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    text = transcript.decode("utf-8", errors="ignore").lower()
    if proc.returncode != 0:
        raise AssertionError(f"{name}: expected rc=0, got rc={proc.returncode}\n{text}")
    if "traceback" in text:
        raise AssertionError(f"{name}: traceback detected\n{text}")
    if "setupterm" in text:
        raise AssertionError(f"{name}: setupterm failure detected\n{text}")
    if "authored-pack-tui: error:" in text:
        raise AssertionError(f"{name}: unexpected tui error detected\n{text}")
    for needle in required_substrings or []:
        if needle.lower() not in text:
            tail = text[-4000:]
            raise AssertionError(f"{name}: missing transcript marker {needle!r}\n{tail}")
    print(f"ok: {name}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="authored-pack-pty-smoke-") as tmp:
        tmp_path = Path(tmp)
        input_dir = tmp_path / "input"
        out_dir = tmp_path / "out"
        input_dir.mkdir()
        (input_dir / "note.txt").write_text("demo\n", encoding="utf-8")
        (input_dir / "sample.bin").write_bytes(b"\x00\x01\x02")

        cases = [
            (
                "calm-start-quit",
                {
                    "argv": list(BASE_CMD),
                    "actions": [("Help: start here", b"q")],
                },
            ),
            (
                "calm-start-path-cancel-quit",
                {
                    "argv": list(BASE_CMD),
                    "actions": [
                        ("Help: start here", b"j\n"),
                        ("Editing choose folder to pack", b"\x1b"),
                        ("Enter: choose folder", b"q"),
                    ],
                },
            ),
            (
                "calm-sources-dropzone-quit",
                {
                    "argv": list(BASE_CMD),
                    "actions": [
                        ("Help: start here", b"j"),
                        ("START //", b"j"),
                        ("AUTHORED SOURCES //", b"q"),
                    ],
                    "required_substrings": [
                        "authored sources // stage items for next assemble",
                        "drop zone // drop files and folders",
                        "drop files / folders here",
                    ],
                },
            ),
            (
                "calm-assemble-review-open-close-quit",
                {
                    "argv": list(BASE_CMD),
                    "actions": [
                        ("Help: start here", b"j"),
                        ("START //", b"j"),
                        ("AUTHORED SOURCES //", b"j"),
                        ("4. Enter -> review and assemble", b"\n"),
                        ("confirm what will be written", b"\x1b"),
                        ("4. Enter -> review and assemble", b"q"),
                    ],
                },
            ),
            (
                "calm-folder-review-assemble-verify-quit",
                {
                    "argv": list(BASE_CMD),
                    "actions": [
                        ("Help: start here", b"j\n"),
                        ("Editing choose folder to pack", f"{input_dir}\n".encode("utf-8")),
                        ("Folder chosen", b"o"),
                        ("confirm what will be written", b"\n"),
                        ("Editing choose output folder", f"{out_dir}\n".encode("utf-8")),
                        ("Save folder updated", b"jjjjj\n"),
                        ("RESULT: pack written successfully.", b"j\n"),
                        ("RESULT: pack is self-consistent.", b"q"),
                    ],
                    "required_substrings": [
                        "result: pack written successfully.",
                        "result: pack is self-consistent.",
                    ],
                },
            ),
            (
                "noisy-start-quit",
                {
                    "argv": list(BASE_CMD) + ["--noisy"],
                    "actions": [("Help: start here", b"q")],
                },
            ),
        ]
        for name, cfg in cases:
            _run_case(name, **cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
