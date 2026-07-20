from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

from authored_pack import cli


ROOT = Path(__file__).resolve().parents[1]


def load_tui_module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / "authored_pack.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = cli.main(argv)
    return rc, stdout.getvalue(), stderr.getvalue()
