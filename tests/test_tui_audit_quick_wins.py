from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_eps_tui_module():
    spec = importlib.util.spec_from_file_location("eps_tui_audit_quick_wins", ROOT / "bin" / "eps.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RecordingStdScr:
    def __init__(self, inputs: list[int] | None = None) -> None:
        self.inputs = list(inputs or [])
        self.calls: list[tuple[int, int, str, int]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return (24, 80)

    def move(self, *_args, **_kwargs) -> None:
        return None

    def clrtoeol(self) -> None:
        return None

    def refresh(self) -> None:
        return None

    def erase(self) -> None:
        return None

    def addstr(self, y: int, x: int, s: str, attr: int = 0) -> None:
        self.calls.append((y, x, s, attr))

    def getch(self) -> int:
        if self.inputs:
            return self.inputs.pop(0)
        return -1

    def nodelay(self, *_args, **_kwargs) -> None:
        return None

    def timeout(self, *_args, **_kwargs) -> None:
        return None


class TestTuiAuditQuickWins(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load_eps_tui_module()

    def _state(self):
        return self.m.AppState(theme=self.m.Theme(normal=1, reverse=2, header=3))

    def test_prompt_str_curses_supports_ctrl_a_and_ctrl_e(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[ord("a"), ord("b"), ord("c"), 1, ord("X"), 5, ord("Y"), 10])

        result = m._prompt_str_curses(stdscr, "(EPS) path", default="")

        self.assertEqual(result, "XabcY")

    def test_prompt_str_curses_supports_ctrl_u(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[ord("a"), ord("b"), ord("c"), ord(" "), ord("d"), ord("e"), ord("f"), 21, ord("x"), 10])

        result = m._prompt_str_curses(stdscr, "(EPS) note", default="")

        self.assertEqual(result, "x")

    def test_prompt_str_curses_supports_ctrl_w(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[ord("a"), ord("b"), ord("c"), ord(" "), ord("d"), ord("e"), ord("f"), 23, ord("x"), 10])

        result = m._prompt_str_curses(stdscr, "(EPS) note", default="")

        self.assertEqual(result, "abcx")

    def test_normalize_single_path_input_unescapes_finder_dropped_spaces(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            dropped = Path(tmp) / "images 2025"
            dropped.mkdir()

            normalized = m._normalize_single_path_input(str(dropped).replace(" ", "\\ "))

            self.assertEqual(normalized, str(dropped))

    def test_normalize_single_path_input_preserves_plain_spaces(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            dropped = Path(tmp) / "images 2025"
            dropped.mkdir()

            normalized = m._normalize_single_path_input(str(dropped))

            self.assertEqual(normalized, str(dropped))

    def test_normalize_single_path_input_accepts_file_url_drop(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            dropped = Path(tmp) / "pack dir"
            dropped.mkdir()

            normalized = m._normalize_single_path_input(dropped.as_uri())

            self.assertEqual(normalized, str(dropped))

    def test_sample_photo_import_paths_caps_directory_to_seven(self) -> None:
        m = self.m
        paths = [Path(f"/tmp/p{i}.jpg") for i in range(10)]

        with mock.patch.object(m.random, "SystemRandom") as system_random:
            system_random.return_value.sample.return_value = paths[:7]
            chosen = m._sample_photo_import_paths(paths, target_count=7)

        self.assertEqual(len(chosen), 7)
        self.assertEqual(chosen, sorted(paths[:7], key=lambda p: p.as_posix()))

    def test_action_entropy_add_photos_samples_directory_instead_of_importing_all(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            photo_dir = tmp_path / "images 2025"
            photo_dir.mkdir()
            for idx in range(10):
                (photo_dir / f"{idx:02d}.jpg").write_bytes(f"img-{idx}".encode("utf-8"))

            state = self._state()
            with mock.patch.object(m, "_prompt_str_curses", return_value=str(photo_dir)):
                m._action_entropy_add_photos(state, RecordingStdScr())

            self.assertEqual(state.status, "Done.")
            self.assertEqual(len(state.entropy_sources), 7)
            self.assertEqual(state.stamp_config.input_mode, "sources")
            self.assertEqual(state.stamp_config.input_path, "")
            self.assertTrue(any("sampled from 10 image(s)" in line for line in state.log_lines))

    def test_apply_drop_paths_samples_photo_directory_into_sources_mode(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            photo_dir = tmp_path / "images 2025"
            photo_dir.mkdir()
            for idx in range(10):
                (photo_dir / f"{idx:02d}.jpg").write_bytes(f"img-{idx}".encode("utf-8"))

            state = self._state()
            msgs = m._apply_drop_paths(state, [str(photo_dir)], max_apply=7)

            self.assertTrue(any("Photo folder sampled:" in msg for msg in msgs))
            self.assertEqual(len(state.entropy_sources), 7)
            self.assertEqual(state.stamp_config.input_mode, "sources")
            self.assertEqual(state.stamp_config.input_path, "")
            self.assertIsNone(state.last_input_dir)

    def test_action_entropy_add_text_prefers_sources_mode(self) -> None:
        m = self.m
        state = self._state()
        state.stamp_config.input_mode = "folder"
        state.stamp_config.input_path = "/tmp/input"

        with mock.patch.object(m, "_prompt_str_curses", side_effect=["note", "hello"]):
            m._action_entropy_add_text(state, RecordingStdScr())

        self.assertEqual(state.status, "Done.")
        self.assertEqual(len(state.entropy_sources), 1)
        self.assertEqual(state.stamp_config.input_mode, "sources")
        self.assertEqual(state.stamp_config.input_path, "")

    def test_action_entropy_tap_prefers_sources_mode(self) -> None:
        m = self.m
        state = self._state()
        state.stamp_config.input_mode = "folder"
        state.stamp_config.input_path = "/tmp/input"
        stdscr = RecordingStdScr(inputs=[ord("x")] * 16 + [27])

        m._action_entropy_tap(state, stdscr)

        self.assertEqual(state.status, "Entropy collected.")
        self.assertEqual(len(state.entropy_sources), 1)
        self.assertEqual(state.stamp_config.input_mode, "sources")
        self.assertEqual(state.stamp_config.input_path, "")

    def test_deleting_last_source_returns_focus_to_menu(self) -> None:
        m = self.m
        state = self._state()
        state.focus = "entropy"
        state.entropy_sources.append(SimpleNamespace(kind="text", name="n", sha256="a" * 64, size_bytes=1, meta={}, text="x"))

        m._action_entropy_delete_selected(state)

        self.assertEqual(state.focus, "menu")
        self.assertEqual(state.entropy_sources, [])

    def test_footer_is_rendered_with_reverse_video_and_compact_legend(self) -> None:
        m = self.m
        state = self._state()
        state.status = "Ready."
        state.selected = state.menu.index("Start")
        stdscr = RecordingStdScr()

        m._draw_footer(stdscr, state, 24, 80)

        footer_calls = [call for call in stdscr.calls if call[0] == 23 and call[1] == 0]
        self.assertTrue(footer_calls)
        _, _, line, attr = footer_calls[-1]
        self.assertEqual(attr, state.theme.reverse)
        self.assertIn("Enter: stamp", line)
        self.assertIn("M: mode", line)

    def test_sources_footer_spells_out_delete_and_clear(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Sources")
        state.focus = "entropy"
        state.entropy_sources.append(SimpleNamespace(kind="text", name="n", sha256="a" * 64, size_bytes=1, meta={}, text="x"))
        stdscr = RecordingStdScr()

        m._draw_footer(stdscr, state, 24, 100)

        footer_calls = [call for call in stdscr.calls if call[0] == 23 and call[1] == 0]
        _, _, line, _ = footer_calls[-1]
        self.assertIn("D: delete", line)
        self.assertIn("C: clear", line)

    def test_start_enter_moves_to_stamp_without_running(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Start")

        with mock.patch.object(m, "_run_stamp_from_config", side_effect=AssertionError("should not stamp from Start")):
            keep_running = m.handle_key(RecordingStdScr(), state, m.curses.KEY_ENTER)

        self.assertTrue(keep_running)
        self.assertEqual(state.menu[state.selected], "Stamp")
        self.assertIsNone(state.stamp_panel_draft)

    def test_stamp_requires_explicit_input_choice_before_running(self) -> None:
        m = self.m
        state = self._state()

        m._run_stamp_from_config(state, RecordingStdScr())

        self.assertIn("Set an input folder", state.status)
        self.assertIsNone(state.last_pack_dir)

    def test_verify_enter_without_target_opens_path_edit(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Verify")

        with mock.patch.object(m, "_edit_verify_path", return_value=False) as edit_verify_path:
            keep_running = m.handle_key(RecordingStdScr(), state, m.curses.KEY_ENTER)

        self.assertTrue(keep_running)
        edit_verify_path.assert_called_once()

    def test_clearing_sources_resets_mixed_source_flag(self) -> None:
        m = self.m
        state = self._state()
        state.entropy_sources.append(SimpleNamespace(kind="text", name="n", sha256="a" * 64, size_bytes=1, meta={}, text="x"))
        state.stamp_config.derive_seed = True
        state.stamp_config.mix_sources = True
        state.stamp_panel_draft = m.StampConfig(derive_seed=True, mix_sources=True)

        m._action_entropy_clear(state)

        self.assertFalse(state.stamp_config.mix_sources)
        self.assertFalse(state.stamp_panel_draft.mix_sources if state.stamp_panel_draft is not None else True)

    def test_run_stamp_and_verify_from_config_round_trips_real_pack(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "note.txt").write_text("demo\n", encoding="utf-8")
            (input_dir / "sample.bin").write_bytes(b"\x00\x01\x02")

            state = self._state()
            state.stamp_config = m.StampConfig(
                input_mode="folder",
                input_path=str(input_dir),
                out_path=str(out_dir),
                zip_pack=True,
            )

            m._run_stamp_from_config(state, RecordingStdScr())

            self.assertEqual(state.status, "Done.")
            self.assertIsNotNone(state.last_pack_dir)
            self.assertTrue(state.last_pack_dir is not None and (state.last_pack_dir / "manifest.json").is_file())
            self.assertTrue(any(line == "Stamp complete." for line in state.log_lines))

            state.verify_config = m.VerifyConfig(pack_path=str(state.last_pack_dir), allow_large_manifest=False)
            m._run_verify_from_config(state, RecordingStdScr())

            self.assertEqual(state.status, "Done.")
            self.assertTrue(any(line == "Verify ok." for line in state.log_lines))


if __name__ == "__main__":
    unittest.main()
