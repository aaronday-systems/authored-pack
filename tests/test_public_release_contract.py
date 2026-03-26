from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from eps import __version__


ROOT = Path(__file__).resolve().parents[1]


class TestPublicReleaseContract(unittest.TestCase):
    def test_runtime_and_package_version_match(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["version"], __version__)

    def test_readme_states_public_v1_boundary(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Current release: `v1.0.0`", readme)
        self.assertIn("source-available", readme)
        self.assertIn("not OSI open source", readme)
        self.assertIn("Sealed mode is not implemented in V1", readme)
        self.assertIn("macOS terminals", readme)
        self.assertIn("Linux terminals", readme)
        self.assertIn("best-effort", readme)

    def test_sealed_architecture_doc_is_marked_future_only(self) -> None:
        text = (ROOT / "docs" / "SEALED_PACK_ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("future design only", text)
        self.assertIn("not implemented in EPS v1.0.0", text)

    def test_ci_workflow_exists_for_pytest_and_cli_help(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("pytest -q", workflow)
        self.assertIn("python3 -m eps --help", workflow)

    def test_gitignore_ignores_local_claude_settings(self) -> None:
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".claude/", text)

    def test_internal_process_docs_are_not_shipped(self) -> None:
        for rel in (
            "docs/CHATGPT_PRO_REDTEAM_ENTROPY_DOSSIER.md",
            "docs/CLEAR_DECK_PLAN.md",
            "docs/CROSS_AGENT_CONTROL_PLANE_PROMPT.md",
            "docs/DEVLOG.md",
        ):
            self.assertFalse((ROOT / rel).exists(), msg=rel)

    def test_readme_does_not_reference_internal_contract_paths(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("ssot/ui/", readme)
        self.assertNotIn("fresh, unpredictable bits", readme)

    def test_public_demo_and_copy_assets_exist(self) -> None:
        for rel in (
            "docs/CANONICAL_DEMO.md",
            "docs/PUBLIC_COPY_ASSETS.md",
            "scripts/demo_v1.sh",
        ):
            self.assertTrue((ROOT / rel).is_file(), msg=rel)


if __name__ == "__main__":
    unittest.main()
