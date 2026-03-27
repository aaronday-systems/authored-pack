from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import authored_pack.binmode as binmode_mod

from authored_pack.binmode import BinRecoveryError, stamp_from_source_bin
from authored_pack.pack import verify_pack


class TestStampBin(unittest.TestCase):
    def test_stamp_from_source_bin_consumes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_bin = tmp_path / "source_bin"
            out_dir = tmp_path / "out"
            source_bin.mkdir()

            # Make 60 small files.
            for i in range(60):
                (source_bin / f"e_{i:03d}.bin").write_bytes(f"entropy-{i}".encode("utf-8"))

            res = stamp_from_source_bin(
                source_bin=source_bin,
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
            remaining = len([p for p in source_bin.iterdir() if p.is_file()])
            self.assertEqual(remaining, 53)

    def test_low_watermark_blocks_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_bin = tmp_path / "source_bin"
            out_dir = tmp_path / "out"
            source_bin.mkdir()

            # Only 55 files: consuming 7 would leave 48 (<50).
            for i in range(55):
                (source_bin / f"e_{i:03d}.bin").write_bytes(b"x")

            with self.assertRaises(ValueError):
                stamp_from_source_bin(
                    source_bin=source_bin,
                    out_dir=out_dir,
                    count=7,
                    min_remaining=50,
                    allow_low_bin=False,
                )

    def test_stamp_from_source_bin_preserves_stage_dir_when_recovery_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_bin = tmp_path / "source_bin"
            out_dir = tmp_path / "out"
            source_bin.mkdir()

            originals = []
            for i in range(2):
                p = source_bin / f"e_{i:03d}.bin"
                data = f"entropy-{i}".encode("utf-8")
                p.write_bytes(data)
                originals.append(data)

            orig_move = binmode_mod.shutil.move
            recovery_moves = {"n": 0}

            def fake_move(src, dst, *args, **kwargs):
                src_p = Path(src)
                dst_p = Path(dst)
                if ".authored_pack_stage" in dst_p.parts:
                    return orig_move(src, dst, *args, **kwargs)
                if ".authored_pack_stage" in src_p.parts and source_bin in dst_p.parents:
                    recovery_moves["n"] += 1
                    if recovery_moves["n"] == 2:
                        raise OSError("restore failed")
                    return orig_move(src, dst, *args, **kwargs)
                if ".authored_pack_stage" in src_p.parts and ".authored_pack_failed" in dst_p.parts:
                    return orig_move(src, dst, *args, **kwargs)
                return orig_move(src, dst, *args, **kwargs)

            with patch("authored_pack.binmode.stamp_pack", side_effect=RuntimeError("stamp boom")):
                with patch("authored_pack.binmode.shutil.move", side_effect=fake_move):
                    with self.assertRaises(BinRecoveryError) as cm:
                        stamp_from_source_bin(
                            source_bin=source_bin,
                            out_dir=out_dir,
                            count=2,
                            min_remaining=0,
                            allow_low_bin=True,
                            recursive=True,
                            include_hidden=False,
                            zip_pack=False,
                            derive_seed=False,
                            evidence_bundle=False,
                        )

            self.assertIn(".authored_pack_failed", str(cm.exception))
            failed_root = source_bin / ".authored_pack_failed"
            failed_dirs = [p for p in failed_root.iterdir() if p.is_dir()]
            self.assertEqual(len(failed_dirs), 1, msg=f"failed dirs: {failed_dirs}")
            preserved = failed_dirs[0]
            self.assertTrue(preserved.is_dir())
            combined = [p.read_bytes() for p in source_bin.iterdir() if p.is_file()]
            combined.extend(p.read_bytes() for p in preserved.rglob("*") if p.is_file())
            self.assertCountEqual(combined, originals)

    def test_stamp_from_source_bin_keeps_unrecovered_files_in_failed_stage_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_bin = tmp_path / "source_bin"
            out_dir = tmp_path / "out"
            source_bin.mkdir()

            for i in range(3):
                (source_bin / f"e_{i:03d}.bin").write_bytes(f"entropy-{i}".encode("utf-8"))

            orig_move = binmode_mod.shutil.move
            recovery_moves = {"n": 0}

            def fake_move(src, dst, *args, **kwargs):
                src_p = Path(src)
                dst_p = Path(dst)
                if ".authored_pack_stage" in dst_p.parts:
                    return orig_move(src, dst, *args, **kwargs)
                if ".authored_pack_stage" in src_p.parts and source_bin in dst_p.parents:
                    recovery_moves["n"] += 1
                    if recovery_moves["n"] == 1:
                        raise OSError("restore failed")
                    return orig_move(src, dst, *args, **kwargs)
                if ".authored_pack_stage" in src_p.parts and ".authored_pack_failed" in dst_p.parts:
                    return orig_move(src, dst, *args, **kwargs)
                return orig_move(src, dst, *args, **kwargs)

            with patch("authored_pack.binmode.stamp_pack", side_effect=RuntimeError("stamp boom")):
                with patch("authored_pack.binmode.shutil.move", side_effect=fake_move):
                    with self.assertRaises(BinRecoveryError):
                        stamp_from_source_bin(
                            source_bin=source_bin,
                            out_dir=out_dir,
                            count=3,
                            min_remaining=0,
                            allow_low_bin=True,
                            recursive=True,
                            include_hidden=False,
                            zip_pack=False,
                            derive_seed=False,
                            evidence_bundle=False,
                        )

            failed_root = source_bin / ".authored_pack_failed"
            failed_dirs = [p for p in failed_root.iterdir() if p.is_dir()]
            self.assertEqual(len(failed_dirs), 1, msg=f"failed dirs: {failed_dirs}")
            preserved = failed_dirs[0]
            preserved_files = sorted(p for p in preserved.rglob("*") if p.is_file())
            self.assertGreaterEqual(len(preserved_files), 1)
            remaining_files = sorted(p for p in source_bin.iterdir() if p.is_file())
            all_bytes = {p.read_bytes() for p in preserved_files + remaining_files}
            self.assertEqual(len(all_bytes), 3)


if __name__ == "__main__":
    unittest.main()
