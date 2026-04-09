from __future__ import annotations

import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from authored_pack import __version__


ROOT = Path(__file__).resolve().parents[1]


class TestPublicReleaseContract(unittest.TestCase):
    def test_runtime_and_package_version_match(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["version"], __version__)

    def test_cli_reports_runtime_and_package_version(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        proc = subprocess.run(
            [sys.executable, "-m", "authored_pack", "--version"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"authored-pack {data['project']['version']}")

    def test_readme_states_public_v1_boundary(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Current release: `v1.0.0`", readme)
        self.assertIn("install this repo into an isolated Python environment you already prefer", readme)
        self.assertIn("examples: `pipx`, `uv tool`, or a local virtualenv", readme)
        self.assertIn("authored-pack --help", readme)
        self.assertIn("source-available", readme)
        self.assertIn("not OSI open source", readme)
        self.assertIn("Sealed mode is not implemented in V1", readme)
        self.assertIn("macOS terminals", readme)
        self.assertIn("Linux terminals", readme)
        self.assertIn("best-effort", readme)
        self.assertNotIn("--insane", readme)
        self.assertNotIn("entropy_root_sha256", readme)
        self.assertNotIn("seed_fingerprint_sha256", readme)
        self.assertLess(readme.index("## Quick Start"), readme.index("## Trust Boundary"))

    def test_sealed_architecture_doc_is_marked_future_only(self) -> None:
        text = (ROOT / "docs" / "SEALED_PACK_ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("future design only", text)
        self.assertIn("not implemented in Authored Pack v1.0.0", text)

    def test_ci_workflow_calls_canonical_release_check(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("bash scripts/release_check.sh", workflow)

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
            "scripts/release_check.sh",
            "scripts/smoke_tui_pty.py",
            "scripts/smoke_install.sh",
            "setup.py",
        ):
            self.assertTrue((ROOT / rel).is_file(), msg=rel)

    def test_contributing_points_to_release_check(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("bash scripts/release_check.sh", text)

    def test_public_copy_assets_keep_verify_and_link_language_honest(self) -> None:
        text = (ROOT / "docs" / "PUBLIC_COPY_ASSETS.md").read_text(encoding="utf-8")
        self.assertNotIn("prove what bytes were packaged", text)
        self.assertNotIn("entropy-bearing inputs", text)
        self.assertNotIn("operator-supplied inputs", text)
        self.assertNotIn("Repo + demo: add link", text)
        self.assertNotIn("<add", text)

    def test_public_demo_assets_lead_with_assemble(self) -> None:
        demo_script = (ROOT / "scripts" / "demo_v1.sh").read_text(encoding="utf-8")
        install_smoke = (ROOT / "scripts" / "smoke_install.sh").read_text(encoding="utf-8")
        demo_doc = (ROOT / "docs" / "CANONICAL_DEMO.md").read_text(encoding="utf-8")
        copy_assets = (ROOT / "docs" / "PUBLIC_COPY_ASSETS.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m authored_pack assemble", demo_script)
        self.assertNotIn("python3 -m authored_pack stamp --input", demo_script)
        self.assertIn('"$venv_dir/bin/authored-pack" assemble --input "$input_dir"', install_smoke)
        self.assertIn('"command":"assemble"', install_smoke)
        self.assertNotIn('"$venv_dir/bin/authored-pack" stamp --input "$input_dir"', install_smoke)
        self.assertIn("python3 -m authored_pack assemble", demo_doc)
        self.assertNotIn("python3 -m authored_pack stamp --input", demo_doc)
        self.assertIn("CLI assemble success", copy_assets)
        self.assertIn("Let `assemble` finish", copy_assets)

    def test_release_notes_do_not_reintroduce_operator_input_positioning(self) -> None:
        text = (ROOT / "docs" / "RELEASE_NOTES_v1.0.0.md").read_text(encoding="utf-8")
        self.assertNotIn("operator-supplied inputs", text)

    def test_tui_public_strings_do_not_reintroduce_entropy_positioning(self) -> None:
        text = (ROOT / "bin" / "authored_pack.py").read_text(encoding="utf-8")
        self.assertNotIn("auditable operator-provided entropy", text)
        self.assertNotIn("write entropy source audit into pack", text)
        self.assertNotIn("operator-supplied inputs", text)

    def test_tui_help_hides_legacy_insane_alias_but_parser_still_accepts_it(self) -> None:
        help_proc = subprocess.run(
            [sys.executable, "-B", "bin/authored_pack.py", "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_proc.returncode, 0, msg=help_proc.stderr)
        self.assertIn("Authored Pack", help_proc.stdout)
        self.assertIn("--noisy", help_proc.stdout)
        self.assertNotIn("--insane", help_proc.stdout)
        self.assertNotIn("operator-provided entropy", help_proc.stdout)

        insane_proc = subprocess.run(
            [sys.executable, "-B", "bin/authored_pack.py", "--insane"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("unrecognized arguments", insane_proc.stderr.lower())
        self.assertIn("no tty detected", insane_proc.stderr.lower())


if __name__ == "__main__":
    unittest.main()
