from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from eps import cli


ROOT = Path(__file__).resolve().parents[1]


class TestCliContract(unittest.TestCase):
    def _run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli.main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_stamp_json_emits_success_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            rc, stdout, stderr = self._run_cli(
                [
                    "stamp",
                    "--input",
                    str(input_dir),
                    "--out",
                    str(out_dir),
                    "--json",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], True)
            self.assertEqual(payload["command"], "stamp")
            self.assertIn("result", payload)
            self.assertEqual(payload["result"]["entropy_root_sha256"], payload["result"]["receipt"]["entropy_root_sha256"])
            self.assertTrue(payload["result"]["pack_dir"])

    def test_verify_json_emits_failure_envelope(self) -> None:
        rc, stdout, stderr = self._run_cli(["verify", "--pack", "/no/such/path", "--json"])

        self.assertEqual(rc, 1)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "verify")
        self.assertEqual(payload["error"]["type"], "VerificationError")
        self.assertTrue(payload["error"]["message"])

    def test_stamp_json_rejects_print_seed_with_failure_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            rc, stdout, stderr = self._run_cli(
                [
                    "stamp",
                    "--input",
                    str(input_dir),
                    "--out",
                    str(out_dir),
                    "--derive-seed",
                    "--print-seed",
                    "--json",
                ]
            )

            self.assertEqual(rc, 1)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "stamp")
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertIn("--json cannot be combined", payload["error"]["message"])

    def test_stamp_bin_json_emits_success_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            entropy_bin = tmp_path / "entropy_bin"
            out_dir = tmp_path / "out"
            entropy_bin.mkdir()
            out_dir.mkdir()
            (entropy_bin / "a.bin").write_bytes(b"entropy")

            rc, stdout, stderr = self._run_cli(
                [
                    "stamp-bin",
                    "--entropy-bin",
                    str(entropy_bin),
                    "--out",
                    str(out_dir),
                    "--count",
                    "1",
                    "--min-remaining",
                    "0",
                    "--allow-low-bin",
                    "--json",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], True)
            self.assertEqual(payload["command"], "stamp-bin")
            self.assertEqual(payload["result"]["mode"], "entropy_bin")
            self.assertEqual(payload["result"]["consumed_count"], 1)

    def test_python_module_help_smoke(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "eps", "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("Entropy Pack Stamper", proc.stdout)

    def test_console_script_metadata_present(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = data.get("project", {}).get("scripts", {})
        self.assertEqual(scripts.get("eps"), "eps.cli:main")


if __name__ == "__main__":
    unittest.main()
