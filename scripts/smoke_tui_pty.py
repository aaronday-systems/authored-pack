#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
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


def _drain_fd(fd: int, transcript: bytearray, *, timeout_s: float) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], min(0.05, remaining))
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        transcript.extend(chunk)


def _run_case(name: str, *, argv: list[str], actions: list[tuple[float, bytes]], rows: int = 24, cols: int = 80) -> None:
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
        for delay_s, payload in actions:
            _drain_fd(master_fd, transcript, timeout_s=delay_s)
            os.write(master_fd, payload)

        deadline = time.monotonic() + 5.0
        while proc.poll() is None and time.monotonic() < deadline:
            _drain_fd(master_fd, transcript, timeout_s=0.05)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
            raise AssertionError(f"{name}: timed out waiting for TUI to exit")

        _drain_fd(master_fd, transcript, timeout_s=0.1)
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
    print(f"ok: {name}")


def main() -> int:
    cases = [
        (
            "calm-start-quit",
            {
                "argv": list(BASE_CMD),
                "actions": [(0.35, b"q")],
            },
        ),
        (
            "calm-start-path-cancel-quit",
            {
                "argv": list(BASE_CMD),
                "actions": [(0.35, b"j\n"), (0.25, b"\x1b"), (0.2, b"q")],
            },
        ),
        (
            "calm-stamp-review-open-close-quit",
            {
                "argv": list(BASE_CMD),
                "actions": [(0.35, b"jjj\n"), (0.25, b"\x1b"), (0.2, b"q")],
            },
        ),
        (
            "noisy-start-quit",
            {
                "argv": list(BASE_CMD) + ["--noisy"],
                "actions": [(0.5, b"q")],
            },
        ),
    ]
    for name, cfg in cases:
        _run_case(name, **cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
