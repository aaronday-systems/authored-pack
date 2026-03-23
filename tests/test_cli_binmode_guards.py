from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from eps import cli
from eps.binmode import stamp_from_entropy_bin


class TestCliBinmodeGuards(unittest.TestCase):
    def _run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli.main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_stamp_rejects_overlapping_input_and_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = input_dir / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            rc, stdout, stderr = self._run_cli(
                [
                    "stamp",
                    "--input",
                    str(input_dir),
                    "--out",
                    str(out_dir),
                ]
            )

            self.assertEqual(rc, 2)
            self.assertEqual(stdout, "")
            self.assertIn("must not overlap", stderr)
            self.assertFalse(out_dir.exists())

    def test_stamp_json_rejects_overlapping_input_and_out_with_failure_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = input_dir / "out"
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

            self.assertEqual(rc, 1)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["command"], "stamp")
            self.assertEqual(payload["error"]["type"], "ValueError")
            self.assertIn("must not overlap", payload["error"]["message"])
            self.assertFalse(out_dir.exists())

    def test_stamp_rejects_input_nested_under_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "out"
            input_dir = out_dir / "input"
            out_dir.mkdir()
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            rc, stdout, stderr = self._run_cli(
                [
                    "stamp",
                    "--input",
                    str(input_dir),
                    "--out",
                    str(out_dir),
                ]
            )

            self.assertEqual(rc, 2)
            self.assertEqual(stdout, "")
            self.assertIn("must not overlap", stderr)

    def test_stamp_json_rejects_input_nested_under_out_with_failure_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "out"
            input_dir = out_dir / "input"
            out_dir.mkdir()
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

            self.assertEqual(rc, 1)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["command"], "stamp")
            self.assertEqual(payload["error"]["type"], "ValueError")
            self.assertIn("must not overlap", payload["error"]["message"])

    def test_stamp_rejects_json_with_print_seed(self) -> None:
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
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["command"], "stamp")
            self.assertIn("cannot be combined", payload["error"]["message"])
            self.assertEqual(stderr, "")
            self.assertFalse(out_dir.exists())

    def test_stamp_seed_flag_misuse_leaves_output_tree_uncreated(self) -> None:
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
            self.assertFalse(out_dir.exists())

    def test_stamp_json_rejects_write_seed_without_derive_seed(self) -> None:
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
                    "--write-seed",
                    "--json",
                ]
            )

            self.assertEqual(rc, 1)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["command"], "stamp")
            self.assertEqual(payload["error"]["type"], "ValueError")
            self.assertIn("--write-seed requires --derive-seed", payload["error"]["message"])
            self.assertFalse(out_dir.exists())

    def test_stamp_print_seed_without_derive_seed_fails_fast(self) -> None:
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
                    "--print-seed",
                ]
            )

            self.assertEqual(rc, 2)
            self.assertEqual(stdout, "")
            self.assertIn("--print-seed requires --derive-seed", stderr)
            self.assertFalse(out_dir.exists())

    def test_python_module_help_exits_cleanly(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "eps", "--help"],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Entropy Pack Stamper", proc.stdout)

    def test_pyproject_declares_eps_console_script(self) -> None:
        import tomllib

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        scripts = data.get("project", {}).get("scripts", {})
        self.assertEqual(scripts.get("eps"), "eps.cli:main")

    def test_stamp_bin_rejects_overlapping_entropy_bin_and_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            entropy_bin = tmp_path / "entropy_bin"
            out_dir = entropy_bin / "out"
            entropy_bin.mkdir()
            (entropy_bin / "e_001.bin").write_bytes(b"entropy")

            with self.assertRaises(ValueError):
                stamp_from_entropy_bin(
                    entropy_bin=entropy_bin,
                    out_dir=out_dir,
                    count=1,
                    min_remaining=0,
                    allow_low_bin=True,
                    recursive=False,
                    include_hidden=False,
                    zip_pack=False,
                    derive_seed=False,
                    evidence_bundle=False,
                )

            self.assertFalse(out_dir.exists())
            self.assertTrue((entropy_bin / "e_001.bin").is_file())

    def test_stamp_bin_json_rejects_overlapping_entropy_bin_and_out_with_failure_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            entropy_bin = tmp_path / "entropy_bin"
            out_dir = entropy_bin / "out"
            entropy_bin.mkdir()
            (entropy_bin / "e_001.bin").write_bytes(b"entropy")

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

            self.assertEqual(rc, 1)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["command"], "stamp-bin")
            self.assertEqual(payload["error"]["type"], "ValueError")
            self.assertIn("must not overlap", payload["error"]["message"])
            self.assertFalse(out_dir.exists())
            self.assertTrue((entropy_bin / "e_001.bin").is_file())

    def test_stamp_bin_json_low_watermark_failure_emits_failure_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            entropy_bin = tmp_path / "entropy_bin"
            out_dir = tmp_path / "out"
            entropy_bin.mkdir()
            out_dir.mkdir()
            (entropy_bin / "e_001.bin").write_bytes(b"entropy")
            (entropy_bin / "e_002.bin").write_bytes(b"entropy-2")

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
                    "2",
                    "--json",
                ]
            )

            self.assertEqual(rc, 1)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["command"], "stamp-bin")
            self.assertEqual(payload["error"]["type"], "ValueError")
            self.assertIn("low-watermark", payload["error"]["message"])
            self.assertTrue((entropy_bin / "e_001.bin").is_file())
            self.assertTrue((entropy_bin / "e_002.bin").is_file())


if __name__ == "__main__":
    unittest.main()
