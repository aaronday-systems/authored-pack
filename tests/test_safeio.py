from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from authored_pack.safeio import trusted_copy_with_sha256


class TestSafeIo(unittest.TestCase):
    def test_failed_source_open_closes_temporary_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dst = root / "dst.bin"
            dst.write_bytes(b"existing destination")
            created = []
            mkstemp = tempfile.mkstemp

            def track_tempfile(*args, **kwargs):
                result = mkstemp(*args, **kwargs)
                created.append(result)
                return result

            try:
                with patch("authored_pack.safeio.tempfile.mkstemp", side_effect=track_tempfile):
                    with self.assertRaises(ValueError):
                        trusted_copy_with_sha256(root / "missing.bin", dst)

                self.assertEqual(len(created), 1)
                fd, temp_name = created[0]
                with self.assertRaises(OSError) as caught:
                    os.fstat(fd)
                self.assertEqual(caught.exception.errno, errno.EBADF)
                self.assertFalse(Path(temp_name).exists())
                self.assertEqual(dst.read_bytes(), b"existing destination")
            finally:
                for fd, _name in created:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def test_trusted_copy_replaces_symlink_destination_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src.bin"
            dst = tmp_path / "dst.bin"
            target = tmp_path / "target.bin"

            src.write_bytes(b"source-bytes")
            target.write_bytes(b"target-bytes")
            try:
                dst.symlink_to(target)
            except OSError:
                self.skipTest("symlinks not supported on this platform")

            digest, size = trusted_copy_with_sha256(src, dst)

            self.assertEqual(size, len(b"source-bytes"))
            self.assertEqual(dst.read_bytes(), b"source-bytes")
            self.assertEqual(target.read_bytes(), b"target-bytes")
            self.assertFalse(dst.is_symlink())
            self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
