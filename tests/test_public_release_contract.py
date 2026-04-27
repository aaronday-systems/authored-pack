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
        self.assertEqual(data["project"]["license"], "Apache-2.0")
        pkg_info = (ROOT / "authored_pack.egg-info" / "PKG-INFO").read_text(encoding="utf-8")
        self.assertIn(f"Version: {__version__}", pkg_info)
        self.assertIn("Current release: `v0.2.4`", pkg_info)
        self.assertIn("docs/RELEASE_NOTES_v0.2.4.md", pkg_info)
        self.assertIn("License-Expression: Apache-2.0", pkg_info)
        self.assertNotIn("proprietary code", pkg_info)
        self.assertNotIn("Current release: `v1.0.0`", pkg_info)

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

    def test_readme_states_current_public_release_boundary(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Current release: `v0.2.4`", readme)
        self.assertIn("git clone https://github.com/aaronday-systems/authored-pack.git", readme)
        self.assertIn("bash scripts/demo_v1.sh", readme)
        self.assertIn("python3 -m authored_pack assemble --input ./my_case --out ./out --zip", readme)
        self.assertIn("Most first-time users can start with `python3 -m authored_pack`", readme)
        self.assertIn("Use the TUI when you want to stage notes, photos, or other manual sources", readme)
        self.assertIn("open source under Apache License 2.0", readme)
        self.assertIn("OSI open source", readme)
        self.assertIn("deterministic pack you can verify later", readme)
        self.assertIn("## Why This Exists", readme)
        self.assertIn("Entropy Pack Stamper", readme)
        self.assertIn("came out of earlier work on agent seed state", readme)
        self.assertIn("The name was wrong.", readme)
        self.assertIn("Not an entropy source. Not a proof system. Not an attestation engine.", readme)
        self.assertIn("## Suggested Use Cases", readme)
        self.assertIn("Keep this list narrow. These are the strongest current fits.", readme)
        self.assertIn("`Bug repro bundle`", readme)
        self.assertIn("`CI failure packet`", readme)
        self.assertIn("`QA regression fixture pack`", readme)
        self.assertIn("`Field capture packet`", readme)
        self.assertIn("`Verified human-to-agent handoff`", readme)
        self.assertNotIn("`Debug session freeze`", readme)
        self.assertNotIn("`Design review packet`", readme)
        self.assertNotIn("`Lab run bundle`", readme)
        self.assertIn("## Use It When", readme)
        self.assertIn("you want to hand off a bounded folder to another engineer or agent", readme)
        self.assertIn("## Don't Use It When", readme)
        self.assertIn("you need signed provenance or attestation", readme)
        self.assertIn("## Core Mental Model", readme)
        self.assertIn("Think packet, not archive:", readme)
        self.assertIn("## Pack Roots", readme)
        self.assertIn("## Share Surfaces", readme)
        self.assertIn("## For Automation and Agents", readme)
        self.assertIn("`verify` and `inspect` enforce operator caps", readme)
        self.assertIn("`--max-manifest-mib`, `--max-artifact-mib`, and `--max-total-mib`", readme)
        self.assertIn("`assemble` remains unconstrained", readme)
        self.assertIn("inspect --json --roots-only", readme)
        self.assertIn("macOS terminals", readme)
        self.assertIn("Linux terminals", readme)
        self.assertIn("best-effort", readme)
        self.assertNotIn("source-available under the Aaron Day license", readme)
        self.assertNotIn("not OSI open source", readme)
        self.assertNotIn("Aaron Day license", readme)
        self.assertNotIn("pipx", readme)
        self.assertNotIn("uv tool", readme)
        self.assertNotIn("authored-pack --help", readme)
        self.assertNotIn("--insane", readme)
        self.assertNotIn("entropy_root_sha256", readme)
        self.assertNotIn("seed_fingerprint_sha256", readme)
        self.assertNotIn("Use the canonical noun first", readme)
        self.assertNotIn("entropy-pack-stamper idea", readme)
        self.assertNotIn("public v1 is", readme.lower())
        self.assertNotIn("It does not create entropy.", readme)
        self.assertNotIn("assembling, verifying, inspecting, and exporting", readme)
        self.assertLess(readme.index("## Quick Start"), readme.index("## Why This Exists"))
        self.assertLess(readme.index("## Quick Start"), readme.index("## Core Mental Model"))
        self.assertLess(readme.index("## Core Mental Model"), readme.index("## Why This Exists"))
        self.assertLess(readme.index("## Why This Exists"), readme.index("## Trust Boundary"))
        self.assertLess(readme.index("## Use It When"), readme.index("## What You Get"))
        self.assertLess(readme.index("## Pack Roots"), readme.index("## Verify"))
        self.assertLess(readme.index("## Share Surfaces"), readme.index("## Verify"))
        self.assertLess(readme.index("## For Automation and Agents"), readme.index("## Consume Bin"))
        self.assertLess(readme.index("## JSON Contract"), readme.index("## Suggested Use Cases"))
        self.assertLess(readme.index("## Suggested Use Cases"), readme.index("## Public Release Boundary"))

    def test_security_policy_uses_current_product_identity(self) -> None:
        text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("Authored Pack is a deterministic packaging and verification tool.", text)
        self.assertIn("The current supported public line is:\n- `v0.2.x`", text)
        self.assertIn("Older pre-`v0.2.4` states are historical development milestones", text)
        self.assertIn("## What Authored Pack Does And Does Not Promise", text)
        self.assertNotIn("EPS is a deterministic packaging and verification tool.", text)
        self.assertNotIn("## What EPS Does And Does Not Promise", text)

    def test_changelog_reflects_current_authored_pack_release(self) -> None:
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("All notable public-release changes to Authored Pack will be documented here.", text)
        self.assertIn("## [0.2.4] - 2026-04-15", text)
        self.assertIn("packet mental model", text)
        self.assertLess(text.index("## [0.2.4] - 2026-04-15"), text.index("## [0.2.3] - 2026-04-14"))
        self.assertNotIn("pending public release", text)
        self.assertNotIn("changes to EPS", text)

    def test_sealed_architecture_doc_is_marked_future_only(self) -> None:
        text = (ROOT / "docs" / "SEALED_PACK_ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("future design only", text)
        self.assertIn("not implemented in Authored Pack v0.2.4", text)
        self.assertIn("future-design one-line description for sealed mode only, not the current public pitch", text)

    def test_ci_workflow_calls_canonical_release_check(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("bash scripts/release_check.sh", workflow)

    def test_release_check_requires_clean_tracked_tree(self) -> None:
        text = (ROOT / "scripts" / "release_check.sh").read_text(encoding="utf-8")
        self.assertIn("git diff --quiet --ignore-submodules --", text)
        self.assertIn("git diff --cached --quiet --ignore-submodules --", text)
        self.assertIn("tracked worktree must be clean", text)

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
        self.assertNotIn("SEALED_PACK_ARCHITECTURE.md", readme)

    def test_public_demo_and_copy_assets_exist(self) -> None:
        for rel in (
            "docs/CANONICAL_DEMO.md",
            "docs/PUBLIC_COPY_ASSETS.md",
            "docs/RELEASE_NOTES_v0.2.4.md",
            "scripts/demo_v1.sh",
            "scripts/release_check.sh",
            "scripts/smoke_tui_pty.py",
            "scripts/smoke_install.sh",
            "setup.py",
        ):
            self.assertTrue((ROOT / rel).is_file(), msg=rel)
        self.assertFalse((ROOT / "docs" / "RELEASE_NOTES_v1.0.0.md").exists())

    def test_contributing_points_to_release_check(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("bash scripts/release_check.sh", text)

    def test_repo_has_public_voice_brief_and_release_template(self) -> None:
        brief = (ROOT / "docs" / "briefs" / "public-voice.md").read_text(encoding="utf-8")
        self.assertIn("Owner: Dev Architect", brief)
        self.assertIn("1. origin question", brief)
        self.assertIn("2. first practical response", brief)
        self.assertIn("3. explicit correction", brief)
        self.assertIn("4. durable core", brief)
        self.assertIn("5. hard boundary", brief)
        self.assertIn("The name was wrong.", brief)
        self.assertIn("Not an entropy source. Not a proof system. Not an attestation engine.", brief)
        self.assertIn("platform", brief)
        self.assertIn("tests/test_public_release_contract.py", brief)
        self.assertIn("scripts/release_check.sh", brief)

        template = (ROOT / "docs" / "RELEASE_NOTES_TEMPLATE.md").read_text(encoding="utf-8")
        self.assertIn("# Authored Pack vX.Y.Z Release Notes", template)
        self.assertIn("## Release Summary", template)
        self.assertIn("## Product Boundary", template)
        self.assertIn("## Public Contract Highlights", template)
        self.assertIn("## Trust Boundary Notes", template)
        self.assertIn("## Release Verification", template)
        self.assertIn("bash scripts/release_check.sh", template)

    def test_agents_points_public_surface_work_to_voice_contract(self) -> None:
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("docs/briefs/public-voice.md", text)
        self.assertIn("docs/RELEASE_NOTES_TEMPLATE.md", text)
        self.assertIn("Dev Architect owns the public voice contract", text)

    def test_public_copy_assets_keep_verify_and_link_language_honest(self) -> None:
        text = (ROOT / "docs" / "PUBLIC_COPY_ASSETS.md").read_text(encoding="utf-8")
        self.assertNotIn("prove what bytes were packaged", text)
        self.assertNotIn("entropy-bearing inputs", text)
        self.assertNotIn("operator-supplied inputs", text)
        self.assertNotIn("Repo + demo: add link", text)
        self.assertNotIn("<add", text)
        self.assertNotIn("route it into another tool", text)

    def test_public_demo_assets_lead_with_assemble(self) -> None:
        demo_script = (ROOT / "scripts" / "demo_v1.sh").read_text(encoding="utf-8")
        install_smoke = (ROOT / "scripts" / "smoke_install.sh").read_text(encoding="utf-8")
        demo_doc = (ROOT / "docs" / "CANONICAL_DEMO.md").read_text(encoding="utf-8")
        copy_assets = (ROOT / "docs" / "PUBLIC_COPY_ASSETS.md").read_text(encoding="utf-8")
        self.assertIn('python_bin="${PYTHON_BIN:-python3}"', demo_script)
        self.assertIn('"$python_bin" -m authored_pack assemble', demo_script)
        self.assertIn('"$python_bin" -m authored_pack inspect --pack "$zip_path" --json', demo_script)
        self.assertIn('INSPECT_JSON="$inspect_json"', demo_script)
        self.assertIn('"$python_bin" -m authored_pack verify --pack "$zip_path"', demo_script)
        self.assertIn("Next on your own folder:", demo_script)
        self.assertNotIn('"$python_bin" -m authored_pack stamp --input', demo_script)
        self.assertNotIn('"$python_bin" -m authored_pack verify --pack "$pack_dir"', demo_script)
        self.assertNotIn("inspect=%s", demo_script)
        self.assertIn("hello from Authored Pack", demo_script)
        self.assertNotIn("hello from EPS", demo_script)
        self.assertIn('repo_cli assemble --input "$input_dir" --out "$out_dir" --zip --json', install_smoke)
        self.assertIn('assert payload["command"] == "assemble"', install_smoke)
        self.assertIn('assert result["pack_type"] == "zip"', install_smoke)
        self.assertIn('repo_cli_smoke_consumer=', install_smoke)
        self.assertNotIn('pip install "$ROOT"', install_smoke)
        self.assertIn("Run this from repo root:", demo_doc)
        self.assertIn("bash scripts/demo_v1.sh", demo_doc)
        self.assertIn("python3 -m authored_pack assemble", demo_doc)
        self.assertIn("python3 -m authored_pack verify --pack /path/to/authored_pack.zip", demo_doc)
        self.assertIn("python3 -m authored_pack inspect --pack /path/to/authored_pack.zip --json", demo_doc)
        self.assertIn("Next on your own folder:", demo_doc)
        self.assertNotIn("python3 -m authored_pack stamp --input", demo_doc)
        self.assertNotIn('find "$tmp/out" -mindepth 1 -maxdepth 1 -type d | head -n 1', demo_doc)
        self.assertIn("CLI assemble success", copy_assets)
        self.assertIn("Let `assemble` finish", copy_assets)

    def test_release_notes_do_not_reintroduce_operator_input_positioning(self) -> None:
        text = (ROOT / "docs" / "RELEASE_NOTES_v0.2.4.md").read_text(encoding="utf-8")
        self.assertNotIn("operator-supplied inputs", text)

    def test_release_notes_match_current_public_surface(self) -> None:
        text = (ROOT / "docs" / "RELEASE_NOTES_v0.2.4.md").read_text(encoding="utf-8")
        self.assertIn("Date: 2026-04-15", text)
        self.assertIn("Status: released", text)
        self.assertIn("- `assemble`", text)
        self.assertIn("- `inspect`", text)
        self.assertIn("- `consume-bin`", text)
        self.assertIn("Apache License 2.0", text)
        self.assertIn("JSON CLI envelopes for `assemble`, `verify`, `inspect`, and `consume-bin`", text)
        self.assertIn("compatibility aliases remain available for `stamp` and `stamp-bin`", text)
        self.assertIn("Release verification used for `v0.2.4`", text)
        self.assertIn("clean tracked worktree", text)
        self.assertIn("bash scripts/release_check.sh", text)
        self.assertNotIn("Status: public release target", text)
        self.assertNotIn("Before tagging `v0.2.4`, confirm:", text)
        self.assertNotIn("- `stamp`\n- `verify`\n- `stamp-bin`", text)

    def test_historical_release_notes_are_fenced_as_legacy(self) -> None:
        text = (ROOT / "docs" / "RELEASE_NOTES_v0.2.0.md").read_text(encoding="utf-8")
        self.assertIn("Historical note: this is an older EPS-era archive", text)
        self.assertIn("It is not the current public contract", text)

    def test_internal_handoff_docs_are_marked_maintainer_only(self) -> None:
        for rel in (
            "docs/authored_pack_plan_2026-03-30.md",
            "docs/repo_architect_handoff_2026-03-30.md",
            "docs/dev_architect_handoff_2026-04-09.md",
            "docs/claude_first_time_developer_review_2026-04-11.md",
            "docs/claude_final_first_time_developer_review_2026-04-11.md",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("Maintainer note:", text, msg=rel)
            self.assertIn("not part of the current public product contract", text, msg=rel)

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
