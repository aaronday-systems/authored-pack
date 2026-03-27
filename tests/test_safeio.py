from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from authored_pack.safeio import trusted_copy_with_sha256


class TestSafeIo(unittest.TestCase):
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
