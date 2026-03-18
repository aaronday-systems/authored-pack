from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_eps_tui_module():
    spec = importlib.util.spec_from_file_location("eps_tui_p1_regressions", ROOT / "bin" / "eps.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DummyStdScr:
    def getmaxyx(self) -> tuple[int, int]:
        return (24, 80)

    def move(self, *_args, **_kwargs) -> None:
        return None

    def clrtoeol(self) -> None:
        return None

    def refresh(self) -> None:
        return None


class TestTuiP1Regressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load_eps_tui_module()

    def test_build_sources_payload_dir_fails_closed_on_photo_drift(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            photo = tmp_path / "photo.jpg"
            photo.write_bytes(b"abc")
            src = m.EntropySource(
                kind="photo",
                name="photo.jpg",
                sha256=hashlib.sha256(b"abc").hexdigest(),
                size_bytes=3,
                path=photo,
            )
            photo.write_bytes(b"abcd")

            with self.assertRaises(ValueError):
                m._build_sources_payload_dir([src])

    def test_action_stamp_cleans_tmp_payload_dir_when_sources_materialization_fails(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            photo = tmp_path / "photo.jpg"
            photo.write_bytes(b"abc")
            src = m.EntropySource(
                kind="photo",
                name="photo.jpg",
                sha256=hashlib.sha256(b"abc").hexdigest(),
                size_bytes=3,
                path=photo,
            )

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.entropy_sources.append(src)

            prompts = iter(["@sources", str(tmp_path / "out"), "", "", ""])
            created_tmp_dirs: list[Path] = []

            orig_prompt_str = m._prompt_str_curses
            orig_prompt_bool = m._prompt_bool_curses
            orig_mkdtemp = m.tempfile.mkdtemp
            orig_copy2 = m.shutil.copy2

            def fake_prompt_str(*_args, **_kwargs) -> str:
                return next(prompts)

            def fake_prompt_bool(*_args, **_kwargs) -> bool:
                return False

            def recording_mkdtemp(*_args, **_kwargs) -> str:
                path = orig_mkdtemp(*_args, **_kwargs)
                created_tmp_dirs.append(Path(path))
                return path

            def failing_copy2(*_args, **_kwargs):
                raise OSError("copy failed")

            try:
                m._prompt_str_curses = fake_prompt_str
                m._prompt_bool_curses = fake_prompt_bool
                m.tempfile.mkdtemp = recording_mkdtemp
                m.shutil.copy2 = failing_copy2
                m._action_stamp(state, DummyStdScr())
            finally:
                m._prompt_str_curses = orig_prompt_str
                m._prompt_bool_curses = orig_prompt_bool
                m.tempfile.mkdtemp = orig_mkdtemp
                m.shutil.copy2 = orig_copy2

            self.assertEqual(state.status, "Failed.")
            self.assertTrue(any(line == "Stamp failed." for line in state.log_lines))
            self.assertTrue(created_tmp_dirs)
            self.assertFalse(created_tmp_dirs[0].exists())

    def test_poll_drop_dir_retries_transient_failures_until_success(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            drop_dir = tmp_path / "drop"
            drop_dir.mkdir()
            (drop_dir / "landed.txt").write_text("hello", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.drop_dir = drop_dir
            call_count = {"n": 0}

            orig_apply = m._apply_drop_paths

            def fake_apply_drop_paths(_state, paths, *, max_apply=None):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return [f"Text add failed: {paths[0]}: busy"]
                return [f"Text source added: {Path(paths[0]).name}"]

            try:
                m._apply_drop_paths = fake_apply_drop_paths
                m._poll_drop_dir(state)
                self.assertEqual(state.drop_seen, set())
                m._poll_drop_dir(state)
            finally:
                m._apply_drop_paths = orig_apply

            self.assertTrue(any("landed.txt" in msg for msg in state.drop_last_msgs))
            self.assertEqual(len(state.drop_seen), 1)

    def test_audit_writer_skips_unwritable_entries_and_stays_transactional(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            good_text = m.EntropySource(
                kind="text",
                name="note",
                sha256=hashlib.sha256(b"hello").hexdigest(),
                size_bytes=5,
                text="hello",
            )
            missing_photo = m.EntropySource(
                kind="photo",
                name="missing.jpg",
                sha256="b" * 64,
                size_bytes=123,
                path=tmp_path / "missing.jpg",
            )

            audit_dir, warnings = m._write_entropy_sources_into_pack(tmp_path, [good_text, missing_photo])

            self.assertIsNotNone(audit_dir)
            self.assertTrue(audit_dir is not None and audit_dir.is_dir())
            self.assertTrue(warnings)
            index_path = audit_dir / "sources.index.json"
            self.assertTrue(index_path.is_file())
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["kind"], "text")
            self.assertEqual(payload[0]["path"], "001_note.txt")
            self.assertTrue(any("missing.jpg" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
