from __future__ import annotations

import re
from pathlib import Path

SEMVER_RE = re.compile(r"^v\d+\.\d+\.\d+$")
ROOT = Path(__file__).resolve().parents[1]


def _const(name: str, source: str) -> str:
    m = re.search(rf'^{name}\s*=\s*(.+)$', source, flags=re.MULTILINE)
    assert m is not None, f"missing constant: {name}"
    return m.group(1).strip()


def test_authored_pack_tui_header_contract() -> None:
    source = (ROOT / "bin" / "authored_pack.py").read_text(encoding="utf-8")
    assert _const("EPS_TUI_TITLE", source)
    # EPS_TUI_VERSION references APP_VERSION; verify APP_VERSION pattern directly.
    app_version_expr = _const("APP_VERSION", source)
    assert "v" in app_version_expr
    assert "__version__" in app_version_expr
    assert "build_header_identity_line(" in source


def test_eps_header_helper_right_justifies_version() -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("authored_pack_tui", ROOT / "bin" / "authored_pack.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    line = module.build_header_identity_line(
        module.APP_NAME,
        module.EPS_TUI_TITLE,
        "v9.9.9",
        80,
        context_suffix="NEON",
    )
    assert line.endswith(" v9.9.9 ")
    assert "AUTHORED PACK :: Main TUI :: NEON" in line


def test_eps_package_version_is_semver() -> None:
    source = (ROOT / "authored_pack" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^\"]+)"', source, flags=re.MULTILINE)
    assert m is not None
    assert SEMVER_RE.match(f"v{m.group(1)}")
