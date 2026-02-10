from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eps.binmode import stamp_from_entropy_bin
from eps.pack import verify_pack


class TestStampBin(unittest.TestCase):
    def test_stamp_from_entropy_bin_consumes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            entropy_bin = tmp_path / "entropy_bin"
            out_dir = tmp_path / "out"
            entropy_bin.mkdir()

            # Make 60 small files.
            for i in range(60):
                (entropy_bin / f"e_{i:03d}.bin").write_bytes(f"entropy-{i}".encode("utf-8"))

            res = stamp_from_entropy_bin(
                entropy_bin=entropy_bin,
                out_dir=out_dir,
                count=7,
                min_remaining=50,
                allow_low_bin=False,
                recursive=True,
                include_hidden=False,
                zip_pack=True,
                derive_seed=True,
                evidence_bundle=True,
            )

            self.assertTrue(res.stamp.pack_dir.is_dir())
            self.assertEqual(len(res.consumed), 7)

            # Verify pack is valid.
            vr = verify_pack(res.stamp.pack_dir)
            self.assertTrue(vr.ok, msg=f"errors: {vr.errors}")
            self.assertEqual(vr.root_sha256, res.stamp.root_sha256)

            # Bin should have 53 files remaining (best-effort after_count is used).
            remaining = len([p for p in entropy_bin.iterdir() if p.is_file()])
            self.assertEqual(remaining, 53)

    def test_low_watermark_blocks_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            entropy_bin = tmp_path / "entropy_bin"
            out_dir = tmp_path / "out"
            entropy_bin.mkdir()

            # Only 55 files: consuming 7 would leave 48 (<50).
            for i in range(55):
                (entropy_bin / f"e_{i:03d}.bin").write_bytes(b"x")

            with self.assertRaises(ValueError):
                stamp_from_entropy_bin(
                    entropy_bin=entropy_bin,
                    out_dir=out_dir,
                    count=7,
                    min_remaining=50,
                    allow_low_bin=False,
                )


if __name__ == "__main__":
    unittest.main()
