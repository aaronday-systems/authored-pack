from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import authored_pack.pack as pack_module
from authored_pack.pack import assemble_pack, verify_pack


class TestContainmentAndBounds(unittest.TestCase):
    def test_stream_hash_bounds_each_read_and_counts_multiple_chunks(self) -> None:
        data = b"x" * (1024 * 1024 + 7)

        class BoundedReader(io.BytesIO):
            def __init__(self, content: bytes, largest_read: int):
                super().__init__(content)
                self.largest_read = largest_read

            def read(self, size=-1):
                if size is None or not 0 <= size <= self.largest_read:
                    raise AssertionError(f"unbounded read requested: {size}")
                return super().read(size)

        for cap in (None, len(data)):
            with self.subTest(cap=cap), BoundedReader(data, 1024 * 1024) as reader:
                digest, size = pack_module._sha256_hex_stream(reader, max_bytes=cap)
                self.assertEqual(size, len(data))
                self.assertEqual(digest, hashlib.sha256(data).hexdigest())

        with BoundedReader(data, 6) as reader:
            with self.assertRaisesRegex(ValueError, "stream exceeded max_bytes"):
                pack_module._sha256_hex_stream(reader, max_bytes=5)
            self.assertEqual(reader.tell(), 6)

    def _input(self, root: Path) -> Path:
        input_dir = root / "input"
        input_dir.mkdir()
        (input_dir / "a.txt").write_text("hello", encoding="utf-8")
        return input_dir

    def test_content_addressed_pack_symlink_is_rejected_before_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path)
            reference = assemble_pack(input_dir=input_dir, out_dir=tmp_path / "reference")
            out = tmp_path / "out"
            out.mkdir()
            (out / reference.root_sha256).symlink_to(reference.pack_dir, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "content-addressed pack path is a symlink"):
                assemble_pack(input_dir=input_dir, out_dir=out)

    def test_output_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path)
            out = tmp_path / "out"
            escaped = tmp_path / "escaped"
            with patch.object(pack_module, "_pack_dir_for_root", return_value=escaped):
                with self.assertRaisesRegex(ValueError, "escapes requested output directory"):
                    assemble_pack(input_dir=input_dir, out_dir=out)
            self.assertFalse(escaped.exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_fifo_input_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            input_dir.mkdir()
            os.mkfifo(input_dir / "pipe")
            with self.assertRaisesRegex(ValueError, "non-regular file"):
                assemble_pack(input_dir=input_dir, out_dir=tmp_path / "out")

    def test_payload_closure_stops_after_first_unexpected_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path)
            res = assemble_pack(input_dir=input_dir, out_dir=tmp_path / "out")
            payload = res.pack_dir / "payload"
            for index in range(100):
                (payload / f"unexpected_{index:03d}").mkdir()

            real_scandir = os.scandir
            yielded = 0

            class CountingIterator:
                def __init__(self, iterator):
                    self._iterator = iterator

                def __enter__(self):
                    self._iterator.__enter__()
                    return self

                def __exit__(self, *args):
                    return self._iterator.__exit__(*args)

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal yielded
                    item = next(self._iterator)
                    yielded += 1
                    return item

            def counting_scandir(path):
                return CountingIterator(real_scandir(path))

            with patch.object(pack_module.os, "scandir", side_effect=counting_scandir):
                result = verify_pack(res.pack_dir)
            self.assertFalse(result.ok)
            self.assertIn("unexpected payload files present", result.errors)
            self.assertLessEqual(yielded, 2, msg="closure scan should stop after expected set plus one entry")

    def test_disappearing_payload_is_a_normal_verification_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path)
            res = assemble_pack(input_dir=input_dir, out_dir=tmp_path / "out")
            with patch.object(pack_module, "trusted_sha256_hex", side_effect=FileNotFoundError("gone")):
                result = verify_pack(res.pack_dir)
            self.assertFalse(result.ok)
            self.assertTrue(any("failed to read artifact" in error for error in result.errors), msg=result.errors)


if __name__ == "__main__":
    unittest.main()
