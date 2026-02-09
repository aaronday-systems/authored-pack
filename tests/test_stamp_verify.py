from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from eps.pack import stamp_pack, verify_pack


class TestStampVerify(unittest.TestCase):
    def test_stamp_and_verify_dir_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")
            (input_dir / "sub").mkdir()
            (input_dir / "sub" / "b.bin").write_bytes(b"\x00\x01\x02")

            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                pack_id="test_pack",
                zip_pack=True,
                derive_seed=True,
                write_seed_files=False,
                print_seed=False,
            )
            self.assertTrue(res.pack_dir.is_dir())
            self.assertEqual(len(res.root_sha256), 64)

            vr = verify_pack(res.pack_dir)
            self.assertTrue(vr.ok, msg=f"errors: {vr.errors}")
            self.assertEqual(vr.root_sha256, res.root_sha256)
            self.assertEqual(vr.file_count, 2)

            zip_path = res.pack_dir / "entropy_pack.zip"
            self.assertTrue(zip_path.is_file())
            vz = verify_pack(zip_path)
            self.assertTrue(vz.ok, msg=f"errors: {vz.errors}")
            self.assertEqual(vz.root_sha256, res.root_sha256)
            self.assertEqual(vz.file_count, 2)

            # Idempotent re-stamp: same input should not fail.
            res2 = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                pack_id="test_pack",
                zip_pack=False,
                derive_seed=False,
            )
            self.assertEqual(res2.root_sha256, res.root_sha256)
            self.assertEqual(res2.pack_dir, res.pack_dir)

    def test_verify_rejects_path_traversal_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Path traversal attempt: artifact path escapes pack dir.
            pack_dir = tmp_path / "pack_bad_path"
            pack_dir.mkdir()
            (pack_dir / "manifest.json").write_text(
                '{"schema_version":"entropy.pack.v1","artifacts":[{"path":"../outside.txt","sha256":"%s","size_bytes":1}]}'
                % ("0" * 64),
                encoding="utf-8",
            )
            res = verify_pack(pack_dir)
            self.assertFalse(res.ok)
            self.assertTrue(any("path" in e and "invalid" in e for e in res.errors), msg=f"errors: {res.errors}")

            # Symlink attempt: artifact is a symlink (even if it points inside the pack).
            pack_dir2 = tmp_path / "pack_bad_symlink"
            payload = pack_dir2 / "payload"
            payload.mkdir(parents=True)
            (pack_dir2 / "manifest.json").write_text(
                '{"schema_version":"entropy.pack.v1","artifacts":[{"path":"payload/link.txt","sha256":"%s","size_bytes":5}]}'
                % hashlib.sha256(b"hello").hexdigest(),
                encoding="utf-8",
            )
            (payload / "real.txt").write_text("hello", encoding="utf-8")
            (payload / "link.txt").symlink_to(payload / "real.txt")
            res2 = verify_pack(pack_dir2)
            self.assertFalse(res2.ok)
            self.assertTrue(any("symlink" in e for e in res2.errors), msg=f"errors: {res2.errors}")

            # Zip: invalid artifact path should be rejected without reading arbitrary entries.
            zip_path = tmp_path / "bad.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "manifest.json",
                    '{"schema_version":"entropy.pack.v1","artifacts":[{"path":"../outside.txt","sha256":"%s","size_bytes":1}]}'
                    % ("0" * 64),
                )
            rz = verify_pack(zip_path)
            self.assertFalse(rz.ok)
            self.assertTrue(any("path" in e and "invalid" in e for e in rz.errors), msg=f"errors: {rz.errors}")

    def test_verify_rejects_duplicate_zip_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "dup.zip"

            # Write the same member name twice.
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", '{"schema_version":"entropy.pack.v1","artifacts":[]}')
                zf.writestr("manifest.json", '{"schema_version":"entropy.pack.v1","artifacts":[]}')

            res = verify_pack(zip_path)
            self.assertFalse(res.ok)
            self.assertTrue(any("duplicate" in e for e in res.errors), msg=f"errors: {res.errors}")

    def test_verify_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "pack"
            payload = pack_dir / "payload"
            payload.mkdir(parents=True)
            (pack_dir / "manifest.json").write_text(
                '{"schema_version":"entropy.pack.v1","artifacts":[{"path":"payload/a.txt","sha256":"%s","size_bytes":5}]}'
                % hashlib.sha256(b"hello").hexdigest(),
                encoding="utf-8",
            )
            (payload / "a.txt").write_text("hello", encoding="utf-8")

            # Per-artifact cap.
            r1 = verify_pack(pack_dir, max_artifact_bytes=1)
            self.assertFalse(r1.ok)
            self.assertTrue(any("too large" in e for e in r1.errors), msg=f"errors: {r1.errors}")

            # Manifest cap.
            r2 = verify_pack(pack_dir, max_manifest_bytes=1)
            self.assertFalse(r2.ok)
            self.assertTrue(any("invalid manifest.json" in e for e in r2.errors), msg=f"errors: {r2.errors}")


if __name__ == "__main__":
    unittest.main()
