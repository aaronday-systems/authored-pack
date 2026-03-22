from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eps.pack import stamp_pack, write_evidence_bundle, _write_zip


class TestPackHardening(unittest.TestCase):
    def test_stamp_pack_rejects_overlapping_input_and_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = input_dir / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            with self.assertRaises(ValueError):
                stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)

    def test_stamp_pack_refuses_to_reuse_corrupted_existing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            first = stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=True, derive_seed=False)
            (first.pack_dir / "payload" / "a.txt").write_text("tampered", encoding="utf-8")

            with self.assertRaises(ValueError):
                stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=True, derive_seed=False)

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
                _write_zip(pack_dir, pack_dir / "entropy_pack.zip")

            with self.assertRaises(ValueError):
                write_evidence_bundle(pack_dir)

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

            with patch("eps.pack.trusted_copy_with_sha256", side_effect=corrupting_copy):
                with self.assertRaises(ValueError):
                    stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)

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
                stamp_pack(input_dir=input_dir, out_dir=out_dir, zip_pack=False, derive_seed=False)


if __name__ == "__main__":
    unittest.main()
