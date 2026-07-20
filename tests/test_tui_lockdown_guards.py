from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support import load_tui_module


class TestTuiLockdownGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = load_tui_module("authored_pack_tui_lockdown")

    def test_lockdown_eligible_sources_filters_and_deduplicates(self) -> None:
        m = self.m
        photo = m.AuthoredSource(kind="photo", name="a.jpg", sha256="a" * 64, size_bytes=100)
        photo_dupe = m.AuthoredSource(kind="photo", name="a_copy.jpg", sha256="a" * 64, size_bytes=100)
        tap_low = m.AuthoredSource(kind="tap", name="tap_low", sha256="b" * 64, size_bytes=2048, meta={"events": 3})
        tap_ok = m.AuthoredSource(
            kind="tap",
            name="tap_ok",
            sha256="c" * 64,
            size_bytes=2048,
            meta={"events": int(m.LOCKDOWN_MIN_TAP_EVENTS)},
        )
        bad_sha = m.AuthoredSource(kind="text", name="bad", sha256="not-sha", size_bytes=12, text="x")

        got = m._lockdown_eligible_sources([photo, photo_dupe, tap_low, tap_ok, bad_sha])
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].sha256, "a" * 64)
        self.assertEqual(got[1].sha256, "c" * 64)

    def test_build_sources_payload_dir_fails_fast_on_missing_photo(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.jpg"
            src = m.AuthoredSource(kind="photo", name="missing.jpg", sha256="d" * 64, size_bytes=1, path=missing)
            with self.assertRaises(ValueError):
                m._build_sources_payload_dir([src])


if __name__ == "__main__":
    unittest.main()
