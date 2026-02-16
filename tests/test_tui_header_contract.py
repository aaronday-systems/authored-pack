from __future__ import annotations

import re
from pathlib import Path

SEMVER_RE = re.compile(r"^v\d+\.\d+\.\d+$")
ROOT = Path(__file__).resolve().parents[1]


def _const(name: str, source: str) -> str:
    m = re.search(rf'^{name}\s*=\s*(.+)$', source, flags=re.MULTILINE)
    assert m is not None, f"missing constant: {name}"
    return m.group(1).strip()


def test_eps_tui_header_contract() -> None:
    source = (ROOT / "bin" / "eps.py").read_text(encoding="utf-8")
    assert _const("EPS_TUI_TITLE", source)
    # EPS_TUI_VERSION references APP_VERSION; verify APP_VERSION pattern directly.
    app_version_expr = _const("APP_VERSION", source)
    assert "v" in app_version_expr
    assert "__version__" in app_version_expr
    assert ":: {EPS_TUI_TITLE} {EPS_TUI_VERSION}" in source


def test_eps_package_version_is_semver() -> None:
    source = (ROOT / "eps" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^\"]+)"', source, flags=re.MULTILINE)
    assert m is not None
    assert SEMVER_RE.match(f"v{m.group(1)}")
