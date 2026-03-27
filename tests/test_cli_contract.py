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

from authored_pack import cli
from authored_pack.pack import stamp_pack


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
            self.assertEqual(payload["result"]["pack_root_sha256"], payload["result"]["receipt"]["pack_root_sha256"])
            self.assertNotIn("entropy_root_sha256", payload["result"])
            self.assertNotIn("entropy_root_sha256", payload["result"]["receipt"])
            self.assertTrue(payload["result"]["pack_dir"])

    def test_help_machine_path_uses_non_destructive_json_example(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "authored_pack", "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("authored-pack stamp --input /ABS/PATH/TO/DIR --out ./out --json", proc.stdout)
        self.assertIn("stamp-bin is subtractive", proc.stdout)

    def test_verify_json_emits_failure_envelope(self) -> None:
        rc, stdout, stderr = self._run_cli(["verify", "--pack", "/no/such/path", "--json"])

        self.assertEqual(rc, 1)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "verify")
        self.assertEqual(payload["error"]["type"], "VerificationError")
        self.assertTrue(payload["error"]["message"])
        self.assertEqual(payload["error"]["details"]["pack"], "/no/such/path")
        self.assertIsInstance(payload["error"]["details"]["errors"], list)
        self.assertEqual(payload["error"]["details"]["limits"]["max_manifest_mib"], 4)

    def test_inspect_json_emits_summary_for_pack_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")
            (input_dir / "b.txt").write_text("world", encoding="utf-8")

            stamped = stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=True, derive_seed=True)

            rc, stdout, stderr = self._run_cli(["inspect", "--pack", str(stamped.pack_dir), "--json"])

            self.assertEqual(rc, 0)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], True)
            self.assertEqual(payload["command"], "inspect")
            result = payload["result"]
            self.assertEqual(result["pack_type"], "directory")
            self.assertEqual(result["pack_root_sha256"], stamped.pack_root_sha256)
            self.assertEqual(result["payload_root_sha256"], stamped.payload_root_sha256)
            self.assertEqual(result["artifact_count"], 2)
            self.assertTrue(result["has_receipt"])
            self.assertTrue(result["has_zip"])
            self.assertTrue(result["verification_ok"])
            self.assertIn("receipt_summary", result)
            self.assertIsInstance(result["artifact_preview"], list)

    def test_inspect_json_emits_summary_for_pack_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            stamped = stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=True, derive_seed=False)

            rc, stdout, stderr = self._run_cli(["inspect", "--pack", str(stamped.zip_path), "--json"])

            self.assertEqual(rc, 0)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], True)
            self.assertEqual(payload["command"], "inspect")
            result = payload["result"]
            self.assertEqual(result["pack_type"], "zip")
            self.assertTrue(result["has_zip"])
            self.assertFalse(result["has_evidence_bundle"])
            self.assertTrue(result["verification_ok"])

    def test_inspect_json_missing_pack_emits_failure_envelope(self) -> None:
        rc, stdout, stderr = self._run_cli(["inspect", "--pack", "/no/such/path", "--json"])

        self.assertEqual(rc, 1)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "inspect")
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertIn("unsupported pack path", payload["error"]["message"])

    def test_verify_json_failure_preserves_all_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            stamped = stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)
            (stamped.pack_dir / "pack_root_sha256.txt").write_text(("0" * 64) + "\n", encoding="utf-8")
            (stamped.pack_dir / "payload" / "extra.txt").write_text("extra", encoding="utf-8")

            rc, stdout, stderr = self._run_cli(["verify", "--pack", str(stamped.pack_dir), "--json"])

            self.assertEqual(rc, 1)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["command"], "verify")
            errors = payload["error"]["details"]["errors"]
            self.assertGreaterEqual(len(errors), 2)
            self.assertIn("pack_root_sha256.txt does not match manifest root", errors)
            self.assertTrue(any("unexpected payload files present" in err for err in errors))

    def test_stamp_json_usage_error_emits_failure_envelope(self) -> None:
        rc, stdout, stderr = self._run_cli(["stamp", "--json"])

        self.assertEqual(rc, 1)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "stamp")
        self.assertEqual(payload["error"]["type"], "CliUsageError")
        self.assertIn("required", payload["error"]["message"])

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

    def test_verify_json_usage_error_emits_failure_envelope(self) -> None:
        rc, stdout, stderr = self._run_cli(
            [
                "verify",
                "--pack",
                "/tmp/example.pack",
                "--max-manifest-mib",
                "not-an-int",
                "--json",
            ]
        )

        self.assertEqual(rc, 1)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "verify")
        self.assertEqual(payload["error"]["type"], "CliUsageError")
        self.assertIn("invalid int value", payload["error"]["message"])

    def test_stamp_bin_json_emits_success_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_bin = tmp_path / "source_bin"
            out_dir = tmp_path / "out"
            source_bin.mkdir()
            out_dir.mkdir()
            (source_bin / "a.bin").write_bytes(b"entropy")

            rc, stdout, stderr = self._run_cli(
                [
                    "stamp-bin",
                    "--source-bin",
                    str(source_bin),
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
            self.assertEqual(payload["result"]["mode"], "source_bin")
            self.assertNotIn("entropy_root_sha256", payload["result"])
            self.assertNotIn("entropy_root_sha256", payload["result"]["receipt"])
            self.assertEqual(payload["result"]["consumed_count"], 1)
            self.assertEqual(payload["result"]["warnings"], [])
            self.assertEqual(payload["result"]["policy"]["would_violate_low_watermark"], False)
            self.assertEqual(len(payload["result"]["consumed"]), 1)
            consumed = payload["result"]["consumed"][0]
            self.assertEqual(consumed["src_relpath"], "a.bin")
            self.assertTrue(consumed["src_path"].endswith("a.bin"))
            self.assertTrue(consumed["staged_name"].endswith("__a.bin"))

    def test_stamp_bin_json_success_includes_low_watermark_warning_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_bin = tmp_path / "source_bin"
            out_dir = tmp_path / "out"
            source_bin.mkdir()
            out_dir.mkdir()
            (source_bin / "a.bin").write_bytes(b"entropy")
            (source_bin / "b.bin").write_bytes(b"entropy-2")

            rc, stdout, stderr = self._run_cli(
                [
                    "stamp-bin",
                    "--source-bin",
                    str(source_bin),
                    "--out",
                    str(out_dir),
                    "--count",
                    "1",
                    "--min-remaining",
                    "2",
                    "--allow-low-bin",
                    "--json",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], True)
            self.assertEqual(payload["command"], "stamp-bin")
            self.assertEqual(payload["result"]["policy"]["projected_remaining_after_count"], 1)
            self.assertEqual(payload["result"]["policy"]["min_remaining"], 2)
            self.assertEqual(payload["result"]["policy"]["allow_low_bin"], True)
            self.assertEqual(payload["result"]["policy"]["would_violate_low_watermark"], True)
            self.assertEqual(len(payload["result"]["warnings"]), 1)
            self.assertIn("low-watermark", payload["result"]["warnings"][0])

    def test_json_usage_failure_without_subcommand_reports_eps_command(self) -> None:
        rc, stdout, stderr = self._run_cli(["--json"])

        self.assertEqual(rc, 1)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["command"], "authored-pack")

    def test_bare_cli_prints_help(self) -> None:
        rc, stdout, stderr = self._run_cli([])

        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        self.assertIn("usage: authored-pack", stdout)
        self.assertIn("First clean success", stdout)

    def test_cli_version_flag_prints_runtime_version(self) -> None:
        rc, stdout, stderr = self._run_cli(["--version"])

        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout.strip(), f"authored-pack {cli.__version__}")

    def test_python_module_help_smoke(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "authored_pack", "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("usage: authored-pack", proc.stdout)
        self.assertIn("Authored Pack", proc.stdout)
        self.assertIn("python3 -B bin/authored_pack.py", proc.stdout)
        self.assertIn("not automatic secrecy", proc.stdout)
        self.assertIn("inspect", proc.stdout)
        stamp_help = cli.build_parser()._subparsers._group_actions[0].choices["stamp"].format_help()
        self.assertIn("Emit JSON envelope to stdout", stamp_help)

    def test_verify_json_success_omits_legacy_root_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            stamped = stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)
            rc, stdout, stderr = self._run_cli(["verify", "--pack", str(stamped.pack_dir), "--json"])

            self.assertEqual(rc, 0)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], True)
            self.assertNotIn("entropy_root_sha256", payload["result"])

    def test_human_stamp_output_includes_zip_and_evidence_paths_when_present(self) -> None:
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
                    "--zip",
                    "--evidence-bundle",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertEqual(stderr, "")
            self.assertIn("pack_dir:", stdout)
            self.assertIn("zip_path:", stdout)
            self.assertIn("evidence_bundle_path:", stdout)

    def test_console_script_metadata_present(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = data.get("project", {}).get("scripts", {})
        self.assertEqual(scripts.get("authored-pack"), "authored_pack.cli:main")
        self.assertEqual(len(scripts), 1)


if __name__ == "__main__":
    unittest.main()
