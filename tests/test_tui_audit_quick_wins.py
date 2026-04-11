from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_authored_pack_tui_module():
    spec = importlib.util.spec_from_file_location("authored_pack_tui_audit_quick_wins", ROOT / "bin" / "authored_pack.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RecordingStdScr:
    def __init__(self, inputs: list[int] | None = None, *, size: tuple[int, int] = (24, 80)) -> None:
        self.inputs = list(inputs or [])
        self.size = tuple(size)
        self.calls: list[tuple[int, int, str, int]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return self.size

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
        cls.m = _load_authored_pack_tui_module()

    def _state(self):
        return self.m.AppState(theme=self.m.Theme(normal=1, reverse=2, header=3))

    def test_prompt_str_curses_supports_ctrl_a_and_ctrl_e(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[ord("a"), ord("b"), ord("c"), 1, ord("X"), 5, ord("Y"), 10])

        result = m._prompt_str_curses(stdscr, "(Authored Pack) path", default="")

        self.assertEqual(result, "XabcY")

    def test_prompt_str_curses_supports_ctrl_u(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[ord("a"), ord("b"), ord("c"), ord(" "), ord("d"), ord("e"), ord("f"), 21, ord("x"), 10])

        result = m._prompt_str_curses(stdscr, "(Authored Pack) note", default="")

        self.assertEqual(result, "x")

    def test_prompt_str_curses_supports_ctrl_w(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[ord("a"), ord("b"), ord("c"), ord(" "), ord("d"), ord("e"), ord("f"), 23, ord("x"), 10])

        result = m._prompt_str_curses(stdscr, "(Authored Pack) note", default="")

        self.assertEqual(result, "abcx")

    def test_prompt_str_curses_renders_modal_edit_header_and_hint(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[10])

        result = m._prompt_str_curses(stdscr, "(Authored Pack) choose output folder", default="out")

        joined = "\n".join(call[2] for call in stdscr.calls)
        self.assertEqual(result, "out")
        self.assertIn("Editing choose output folder", joined)
        self.assertIn("Type a value. Enter saves. Esc cancels.", joined)
        self.assertIn("Single q also cancels path prompts.", joined)

    def test_prompt_str_curses_treats_single_q_as_cancel_for_path_prompts(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[ord("q"), 10])

        result = m._prompt_str_curses(stdscr, "(Authored Pack) choose output folder", default="")

        self.assertIsNone(result)

    def test_prompt_str_curses_shortens_folder_defaults_on_narrow_terminals(self) -> None:
        m = self.m
        stdscr = RecordingStdScr(inputs=[10])
        long_default = "/Users/aaronday/Desktop/Screenshot 2026-03-26 at 17.04.28.png"

        m._prompt_str_curses(stdscr, "(Authored Pack) choose output folder", default=long_default)

        joined = "\n".join(call[2] for call in stdscr.calls)
        self.assertIn("...", joined)

    def test_display_path_keeps_home_relative_context_for_absolute_paths(self) -> None:
        m = self.m
        home_child = Path.home() / "Desktop" / "Authored Pack" / "out"

        shown = m._display_path(home_child, max_len=120)

        self.assertEqual(shown, "~/Desktop/Authored Pack/out")

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
            self.assertEqual(len(state.authored_sources), 7)
            self.assertEqual(state.assemble_config.input_mode, "sources")
            self.assertEqual(state.assemble_config.input_path, "")
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
            actions = m._prepare_drop_actions([str(photo_dir)], apply_mode="sources", max_apply=7)
            msgs = m._apply_drop_actions_to_state(state, actions, play_sfx=False)

            self.assertTrue(any("Photo folder sampled:" in msg for msg in msgs))
            self.assertEqual(len(state.authored_sources), 7)
            self.assertEqual(state.assemble_config.input_mode, "sources")
            self.assertEqual(state.assemble_config.input_path, "")
            self.assertIsNone(state.last_input_dir)

    def test_prepare_drop_actions_in_folder_mode_sets_input_dir_not_sources(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            folder = tmp_path / "folder"
            folder.mkdir()
            (folder / "a.txt").write_text("hello", encoding="utf-8")

            state = self._state()
            actions = m._prepare_drop_actions([str(folder)], apply_mode="folder")
            msgs = m._apply_drop_actions_to_state(state, actions, play_sfx=False)

            self.assertTrue(any("Input dir set:" in msg for msg in msgs))
            self.assertEqual(state.authored_sources, [])
            self.assertEqual(state.assemble_config.input_mode, "folder")
            self.assertEqual(state.assemble_config.input_path, str(folder.resolve()))
            self.assertEqual(state.last_input_dir, folder.resolve())

    def test_action_entropy_add_text_prefers_sources_mode(self) -> None:
        m = self.m
        state = self._state()
        state.assemble_config.input_mode = "folder"
        state.assemble_config.input_path = "/tmp/input"

        with mock.patch.object(m, "_prompt_str_curses", side_effect=["note", "hello"]):
            m._action_entropy_add_text(state, RecordingStdScr())

        self.assertEqual(state.status, "Done.")
        self.assertEqual(len(state.authored_sources), 1)
        self.assertEqual(state.assemble_config.input_mode, "sources")
        self.assertEqual(state.assemble_config.input_path, "")

    def test_action_entropy_tap_prefers_sources_mode(self) -> None:
        m = self.m
        state = self._state()
        state.assemble_config.input_mode = "folder"
        state.assemble_config.input_path = "/tmp/input"
        stdscr = RecordingStdScr(inputs=[ord("x")] * 16 + [27])

        m._action_entropy_tap(state, stdscr)

        self.assertEqual(state.status, "Done.")
        self.assertEqual(len(state.authored_sources), 1)
        self.assertEqual(state.assemble_config.input_mode, "sources")
        self.assertEqual(state.assemble_config.input_path, "")

    def test_deleting_last_source_returns_focus_to_menu(self) -> None:
        m = self.m
        state = self._state()
        state.focus = "entropy"
        state.authored_sources.append(SimpleNamespace(kind="text", name="n", sha256="a" * 64, size_bytes=1, meta={}, text="x"))

        m._action_entropy_delete_selected(state)

        self.assertEqual(state.focus, "menu")
        self.assertEqual(state.authored_sources, [])

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
        self.assertIn("Enter: choose folder", line)
        self.assertIn("M: mode", line)

    def test_help_footer_calls_out_viewer_and_readme(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Help")
        stdscr = RecordingStdScr()

        m._draw_footer(stdscr, state, 24, 80)

        footer_calls = [call for call in stdscr.calls if call[0] == 23 and call[1] == 0]
        _, _, line, _ = footer_calls[-1]
        self.assertIn("Down: Start", line)
        self.assertIn("Enter: viewer", line)
        self.assertIn("R: README", line)

    def test_sources_footer_spells_out_delete_and_clear(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Sources")
        state.focus = "entropy"
        state.authored_sources.append(SimpleNamespace(kind="text", name="n", sha256="a" * 64, size_bytes=1, meta={}, text="x"))
        stdscr = RecordingStdScr()

        m._draw_footer(stdscr, state, 24, 100)

        footer_calls = [call for call in stdscr.calls if call[0] == 23 and call[1] == 0]
        _, _, line, _ = footer_calls[-1]
        self.assertIn("D: delete", line)
        self.assertIn("C: clear", line)

    def test_start_footer_stays_actionable_on_narrow_terminal(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Start")
        stdscr = RecordingStdScr(size=(24, 64))

        m._draw_footer(stdscr, state, 24, 64)

        footer_calls = [call for call in stdscr.calls if call[0] == 23 and call[1] == 0]
        _, _, line, _ = footer_calls[-1]
        self.assertIn("Enter: folder", line)
        self.assertIn("Q: quit", line)

    def test_help_footer_teaches_how_to_leave_launch_screen(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Help")
        stdscr = RecordingStdScr()

        m._draw_footer(stdscr, state, 24, 80)

        footer_calls = [call for call in stdscr.calls if call[0] == 23 and call[1] == 0]
        _, _, line, _ = footer_calls[-1]
        self.assertIn("Down: Start", line)
        self.assertIn("Enter: viewer", line)
        self.assertIn("R: README", line)

    def test_init_theme_uses_amber_calm_palette(self) -> None:
        m = self.m
        with mock.patch.object(m.curses, "has_colors", return_value=True), mock.patch.object(m.curses, "start_color"), mock.patch.object(
            m.curses, "use_default_colors"
        ), mock.patch.object(m.curses, "color_pair", side_effect=lambda pair_id: pair_id * 10), mock.patch.object(
            m, "_init_pair_safe"
        ) as init_pair, mock.patch.object(m.curses, "COLORS", 256, create=True), mock.patch.object(m.curses, "COLOR_YELLOW", 3), mock.patch.object(
            m.curses, "COLOR_BLACK", 0
        ):
            theme = m.init_theme()

        init_pair.assert_any_call(1, 172, 0)
        init_pair.assert_any_call(2, 0, 172)
        self.assertEqual(theme.normal, 10)
        self.assertEqual(theme.reverse, 20)
        self.assertEqual(theme.header, 20)

    def test_noisy_failed_stamp_renders_with_failure_palette(self) -> None:
        m = self.m
        state = self._state()
        state.insane = True
        state.palette = m.InsanePalette(bg=[11], header=[12], menu_hot=[13], menu_dim=14, divider=15, text=16, ok=17, warn=18, info=19)
        state.failure_palette = m.InsanePalette(bg=[91, 92], header=[93], menu_hot=[94], menu_dim=95, divider=96, text=97, ok=98, warn=99, info=100)
        state.selected = state.menu.index("Assemble")
        state.status = "Failed."
        state.log_lines = ["ASSEMBLE RESULT // failed", "RESULT: assemble failed."]
        stdscr = RecordingStdScr()

        m.draw(stdscr, state)

        attrs = [call[3] for call in stdscr.calls]
        self.assertIn(94, attrs)
        self.assertIn(96, attrs)
        self.assertIn(97, attrs)

    def test_start_enter_moves_to_assemble_without_running(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Start")

        with mock.patch.object(m, "_run_assemble_from_config", side_effect=AssertionError("should not assemble from Start")), mock.patch.object(
            m, "_edit_assemble_input", return_value=True
        ) as edit_stamp_input:
            keep_running = m.handle_key(RecordingStdScr(), state, m.curses.KEY_ENTER)

        self.assertTrue(keep_running)
        edit_stamp_input.assert_called_once()
        self.assertEqual(state.menu[state.selected], "Assemble")
        self.assertIsNone(state.assemble_panel_draft)
        self.assertEqual(state.current_lane, "folder")
        self.assertEqual(state.assemble_config.input_mode, "folder")

    def test_stamp_requires_explicit_input_choice_before_running(self) -> None:
        m = self.m
        state = self._state()

        m._run_assemble_from_config(state, RecordingStdScr())

        self.assertIn("Set an input folder", state.status)
        self.assertIsNone(state.last_pack_dir)

    def test_sources_enter_with_collected_sources_moves_to_assemble(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Sources")
        state.authored_sources.append(SimpleNamespace(kind="text", name="n", sha256="a" * 64, size_bytes=1, meta={}, text="x"))

        keep_running = m.handle_key(RecordingStdScr(), state, m.curses.KEY_ENTER)

        self.assertTrue(keep_running)
        self.assertEqual(state.menu[state.selected], "Assemble")
        self.assertEqual(state.focus, "menu")
        self.assertIn("Authored Sources selected", state.status)

    def test_sources_import_shortcut_uses_p_not_a(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Sources")

        with mock.patch.object(m, "_action_sources_import_paths") as import_paths:
            keep_running = m.handle_key(RecordingStdScr(), state, ord("a"))

        self.assertTrue(keep_running)
        import_paths.assert_not_called()

        with mock.patch.object(m, "_action_sources_import_paths") as import_paths:
            keep_running = m.handle_key(RecordingStdScr(), state, ord("p"))

        self.assertTrue(keep_running)
        import_paths.assert_called_once_with(state, mock.ANY)

    def test_stamp_preview_ignores_stale_failure_logs_when_not_in_result_state(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Assemble")
        state.status = "Review open."
        state.log_lines = ["ASSEMBLE RESULT // failed", "RESULT: assemble failed."]

        preview = "\n".join(m._selection_preview(state, "Assemble", width=120, height=30))

        self.assertIn("ASSEMBLE // choose input and expected result", preview)
        self.assertNotIn("RESULT: assemble failed.", preview)

    def test_stamp_review_preview_stays_readable_on_narrow_terminal(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Assemble")
        state.assemble_config = m.AssembleConfig(
            input_mode="folder",
            input_path=str(Path.home() / "Desktop" / "Authored Pack" / "very-long-input-folder-name"),
            out_path=str(Path.home() / "Desktop" / "Authored Pack" / "very-long-output-folder-name"),
        )
        m._open_assemble_panel(state)

        preview = m._selection_preview(state, "Assemble", width=64, height=20)
        joined = "\n".join(preview)

        self.assertTrue(all(len(line) <= 64 for line in preview))
        self.assertIn("ASSEMBLE REVIEW // confirm what will be written", joined)
        self.assertIn("Input:", joined)

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
        state.authored_sources.append(SimpleNamespace(kind="text", name="n", sha256="a" * 64, size_bytes=1, meta={}, text="x"))
        state.assemble_config.derive_seed = True
        state.assemble_config.mix_sources = True
        state.assemble_panel_draft = m.AssembleConfig(derive_seed=True, mix_sources=True)

        m._action_entropy_clear(state)

        self.assertFalse(state.assemble_config.mix_sources)
        self.assertFalse(state.assemble_panel_draft.mix_sources if state.assemble_panel_draft is not None else True)

    def test_source_preview_uses_authored_source_title(self) -> None:
        m = self.m
        state = self._state()
        state.authored_sources.append(SimpleNamespace(kind="text", name="note", sha256="a" * 64, size_bytes=1, meta={}, text="hello"))

        m._action_entropy_preview(state)

        self.assertIsNotNone(state.viewer)
        self.assertEqual(state.viewer.title if state.viewer is not None else None, "Authored Source: text")

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
            state.assemble_config = m.AssembleConfig(
                input_mode="folder",
                input_path=str(input_dir),
                out_path=str(out_dir),
                zip_pack=True,
            )

            m._run_assemble_from_config(state, RecordingStdScr())

            self.assertEqual(state.status, "Done.")
            self.assertIsNotNone(state.last_pack_dir)
            self.assertTrue(state.last_pack_dir is not None and (state.last_pack_dir / "manifest.json").is_file())
            self.assertTrue(any(line == "RESULT: pack written successfully." for line in state.log_lines))

            state.verify_config = m.VerifyConfig(pack_path=str(state.last_pack_dir), allow_large_manifest=False)
            m._run_verify_from_config(state, RecordingStdScr())

            self.assertEqual(state.status, "Done.")
            self.assertTrue(any(line == "RESULT: pack is self-consistent." for line in state.log_lines))


if __name__ == "__main__":
    unittest.main()
