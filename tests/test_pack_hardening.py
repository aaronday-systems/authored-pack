from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from authored_pack import pack as pack_module
from authored_pack.pack import assemble_pack, verify_pack, write_evidence_bundle, _write_zip


class TestPackHardening(unittest.TestCase):
    def test_safe_write_text_leaves_no_temp_files_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "pack_root_sha256.txt"

            pack_module._safe_write_text(target, "hello\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")
            self.assertEqual(list(tmp_path.glob(f".{target.name}.*")), [])

    def test_safe_write_json_leaves_no_temp_files_on_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "receipt.json"

            with patch("authored_pack.pack.os.replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    pack_module._safe_write_json(target, {"ok": True})

            self.assertFalse(target.exists())
            self.assertEqual(list(tmp_path.glob(f".{target.name}.*")), [])

    def test_stamp_pack_rejects_overlapping_input_and_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = input_dir / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            with self.assertRaises(ValueError):
                assemble_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)

    def test_stamp_pack_rejects_input_nested_under_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "out"
            input_dir = out_dir / "input"
            out_dir.mkdir()
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            with self.assertRaises(ValueError):
                assemble_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)

    def test_stamp_pack_refuses_to_reuse_corrupted_existing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            first = assemble_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=True, derive_seed=False)
            (first.pack_dir / "payload" / "a.txt").write_text("tampered", encoding="utf-8")

            with self.assertRaises(ValueError):
                assemble_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=True, derive_seed=False)

    def test_stamp_pack_reuse_is_read_only_when_existing_pack_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            first = assemble_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=True,
                derive_seed=True,
                evidence_bundle=True,
            )
            receipt_path = first.pack_dir / "receipt.json"
            zip_path = first.pack_dir / "authored_pack.zip"
            evidence_path = first.evidence_bundle_path
            self.assertIsNotNone(evidence_path)
            assert evidence_path is not None

            receipt_before = receipt_path.read_text(encoding="utf-8")
            receipt_mtime = receipt_path.stat().st_mtime_ns
            zip_mtime = zip_path.stat().st_mtime_ns
            evidence_mtime = evidence_path.stat().st_mtime_ns

            time.sleep(0.01)

            second = assemble_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=True,
                evidence_bundle=False,
            )

            self.assertEqual(second.pack_dir, first.pack_dir)
            self.assertEqual(receipt_path.read_text(encoding="utf-8"), receipt_before)
            self.assertEqual(receipt_path.stat().st_mtime_ns, receipt_mtime)
            self.assertEqual(zip_path.stat().st_mtime_ns, zip_mtime)
            self.assertEqual(evidence_path.stat().st_mtime_ns, evidence_mtime)

    def test_stamp_pack_reuse_materializes_requested_zip_on_existing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            first = assemble_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=False,
                evidence_bundle=False,
            )
            receipt_path = first.pack_dir / "receipt.json"
            receipt_before = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertNotIn("zip_path", receipt_before)
            self.assertFalse((first.pack_dir / "authored_pack.zip").exists())

            second = assemble_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=True,
                derive_seed=False,
                evidence_bundle=False,
            )

            zip_path = first.pack_dir / "authored_pack.zip"
            self.assertEqual(second.pack_dir, first.pack_dir)
            self.assertEqual(second.zip_path, zip_path)
            self.assertTrue(zip_path.is_file())
            self.assertTrue(verify_pack(zip_path).ok)
            receipt_after = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt_after.get("zip_path"), "authored_pack.zip")

    def test_stamp_pack_reuse_materializes_requested_evidence_bundle_on_existing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            first = assemble_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=False,
                evidence_bundle=False,
            )
            expected_path = first.pack_dir / f"authored_evidence_{first.root_sha256}.zip"
            self.assertFalse(expected_path.exists())

            second = assemble_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=False,
                evidence_bundle=True,
            )

            self.assertEqual(second.pack_dir, first.pack_dir)
            self.assertEqual(second.evidence_bundle_path, expected_path)
            self.assertTrue(expected_path.is_file())
            self.assertTrue(bool(second.evidence_bundle_sha256))

    def test_archive_helpers_reject_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "pack"
            payload = pack_dir / "payload"
            payload.mkdir(parents=True)
            (pack_dir / "manifest.json").write_text('{"schema_version":"entropy.pack.v1","artifacts":[]}', encoding="utf-8")
            (payload / "real.txt").write_text("hello", encoding="utf-8")
            (payload / "link.txt").symlink_to(payload / "real.txt")

            with self.assertRaises(ValueError):
                _write_zip(pack_dir, pack_dir / "authored_pack.zip")

            with self.assertRaises(ValueError):
                write_evidence_bundle(pack_dir)

    def test_stamp_pack_writes_private_seed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res = assemble_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=True,
                write_seed_files=True,
            )

            for name in ("seed_master.hex", "seed_master.b64"):
                path = res.pack_dir / name
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_stamp_pack_fails_when_copied_payload_diverges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            def corrupting_copy(src, dst, *args, **kwargs):
                Path(dst).parent.mkdir(parents=True, exist_ok=True)
                Path(dst).write_text("tampered", encoding="utf-8")
                return ("0" * 64, 8)

            with patch("authored_pack.pack.trusted_copy_with_sha256", side_effect=corrupting_copy):
                with self.assertRaises(ValueError):
                    assemble_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)

    def test_stamp_pack_rejects_symlink_source_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            target = input_dir / "target.txt"
            target.write_text("hello", encoding="utf-8")
            link = input_dir / "link.txt"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks not supported on this platform")

            with self.assertRaises(ValueError):
                assemble_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)

    def test_verify_pack_uses_trusted_sha256_for_artifact_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res = assemble_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)

            with patch("authored_pack.pack.trusted_sha256_hex", wraps=pack_module.trusted_sha256_hex) as mocked:
                verified = verify_pack(res.pack_dir)

            self.assertTrue(verified.ok, msg=verified.errors)
            self.assertGreaterEqual(mocked.call_count, 1)

    def test_write_zip_uses_trusted_binary_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res = assemble_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)
            zip_path = res.pack_dir / "authored_pack.zip"

            with patch("authored_pack.pack.trusted_binary_reader", wraps=pack_module.trusted_binary_reader) as mocked:
                _write_zip(res.pack_dir, zip_path)

            self.assertTrue(zip_path.is_file())
            self.assertGreaterEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
