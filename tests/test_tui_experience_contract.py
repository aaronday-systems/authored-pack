from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from itertools import chain, repeat
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_authored_pack_tui_module():
    spec = importlib.util.spec_from_file_location("authored_pack_tui_experience_contract", ROOT / "bin" / "authored_pack.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DummyStdScr:
    def __init__(self, inputs: list[int] | None = None) -> None:
        self.inputs = list(inputs or [])

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

    def addstr(self, *_args, **_kwargs) -> None:
        return None

    def getch(self) -> int:
        if self.inputs:
            return self.inputs.pop(0)
        return -1

    def nodelay(self, *_args, **_kwargs) -> None:
        return None

    def timeout(self, *_args, **_kwargs) -> None:
        return None


class TestTuiExperienceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load_authored_pack_tui_module()

    def _state(self):
        return self.m.AppState(theme=self.m.Theme(normal=0, reverse=0, header=0))

    def _preview(self, label: str, *, width: int = 200, height: int = 40) -> str:
        preview = self.m._selection_preview(self._state(), label, width=width, height=height)
        return "\n".join(preview)

    def test_main_nav_collapses_to_workflow_actions(self) -> None:
        state = self._state()
        self.assertEqual(state.menu[:5], ["Help", "Start", "Sources", "Stamp", "Verify"])
        self.assertEqual(state.menu[state.selected], "Help")
        self.assertNotIn("Experience Mode", state.menu)
        self.assertNotIn("View README", state.menu)
        self.assertNotIn("View TUI Standard", state.menu)
        self.assertNotIn("View TUI Contract", state.menu)

    def test_start_card_leads_with_the_first_success_path(self) -> None:
        joined = self._preview("Start")
        self.assertIn("choose a path and expected result", joined.lower())
        self.assertIn("[FOLDER] Pack a Folder You Already Have", joined)
        self.assertIn("Build a Pack from Collected Sources", joined)
        self.assertIn("[VERIFY] Check a Pack You Already Assembled", joined)
        self.assertIn("Enter = choose folder", joined)
        self.assertNotIn("machine-sidecar route", joined)

    def test_sources_card_prioritizes_empty_state_and_collection_actions(self) -> None:
        joined = self._preview("Sources")
        self.assertIn("AUTHORED SOURCES // stage items for next assemble", joined)
        self.assertIn("STAGED: 0 sources for next assemble", joined)
        self.assertIn("KINDS : photo 0  text 0  tap 0", joined)
        self.assertIn("T = type note text       -> stage text source", joined)
        self.assertIn("Space = tap keys         -> stage tap source", joined)
        self.assertIn("P = import files/folders -> stage photo/text sources", joined)
        self.assertNotIn("A = add photo", joined)
        self.assertIn("DROP ZONE // drop files and folders without typing paths", joined)
        self.assertIn("Watch folder:", joined)
        self.assertIn("Folder now has 0 item(s). Imported this run: 0.", joined)
        self.assertIn("DROP FILES / FOLDERS HERE", joined)
        self.assertIn("RESULT: nothing is written yet.", joined)
        self.assertIn("ENTER: while empty, Enter opens import", joined)
        self.assertIn("ALT: skip this screen", joined)
        self.assertNotIn("ready 0/7", joined.lower())
        self.assertNotIn("lockdown", joined.lower())
        self.assertNotIn("eligible", joined.lower())

    def test_sources_card_keeps_collection_summary_ascii_and_type_counts_visible(self) -> None:
        state = self._state()
        state.authored_sources = [
            SimpleNamespace(kind="photo", name="photo.jpg", sha256="a" * 64, size_bytes=1, meta={}),
            SimpleNamespace(kind="text", name="note", sha256="b" * 64, size_bytes=2, meta={}, text="x"),
            SimpleNamespace(kind="tap", name="tap", sha256="c" * 64, size_bytes=16, meta={"events": 16}),
        ]

        joined = "\n".join(self.m._authored_sources_preview(state, width=200, height=40))
        summary_line = next(line for line in joined.splitlines() if line.startswith("KINDS :"))

        self.assertIn("STAGED: 3 sources for next assemble", joined)
        self.assertIn("KINDS : photo 1  text 1  tap 1", joined)
        self.assertIn("DROP: P imports now |", joined)
        self.assertTrue(all(ord(ch) < 128 for ch in summary_line))

    def test_sources_enter_opens_import_when_empty(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Sources")

        with mock.patch.object(m, "_action_sources_import_paths") as import_paths:
            keep_running = m.handle_key(DummyStdScr(), state, m.curses.KEY_ENTER)

        self.assertTrue(keep_running)
        import_paths.assert_called_once()

    def test_stamp_card_summarizes_review_before_advanced_prompts(self) -> None:
        joined = self._preview("Stamp")
        self.assertIn("ASSEMBLE // choose input and expected result", joined)
        self.assertIn("- Input: not set", joined)
        self.assertIn("- Output folder: out", joined)
        self.assertIn("1. I -> choose input", joined)
        self.assertIn("4. Enter -> review and assemble", joined)
        self.assertIn("YOU GET", joined)
        self.assertIn("- pack folder with manifest.json, receipt.json, and payload/", joined)
        self.assertNotIn("prompt ladder", joined)

    def test_verify_card_focuses_on_integrity_audit(self) -> None:
        joined = self._preview("Verify")
        self.assertIn("WHAT YOU CAN VERIFY", joined)
        self.assertIn("out/<pack_root_sha256>/", joined)
        self.assertIn("RESULT ON SUCCESS", joined)
        self.assertIn("Enter = verify target and show result", joined)
        self.assertIn("- pack: not set", joined)
        self.assertIn("choose a pack and check it", joined.lower())
        self.assertIn("authored_pack.zip", joined)
        self.assertIn("newest pack inside it", joined)

    def test_help_card_is_curated_not_raw_docs(self) -> None:
        joined = self._preview("Help")
        self.assertIn("for humans", joined)
        self.assertIn("MOST COMMON PATH", joined)
        self.assertIn("WORKFLOW", joined)
        self.assertIn("folder  -> Start   -> Assemble -> Verify", joined)
        self.assertIn("sources -> Sources -> Assemble -> Verify", joined)
        self.assertIn("KEY ACTIONS", joined)
        self.assertIn("Press Down for Start", joined)
        self.assertIn("Down = begin", joined)
        self.assertIn("TRUST BOUNDARY", joined)
        self.assertIn("authored_pack.zip", joined)
        self.assertIn("MORE DETAIL", joined)
        self.assertIn("R = README", joined)
        self.assertNotIn("View README", joined)
        self.assertNotIn("View TUI Standard", joined)
        self.assertNotIn("View TUI Contract", joined)
        self.assertNotIn("historical contract", joined)

    def test_help_shortcut_opens_readme_viewer(self) -> None:
        state = self._state()
        state.selected = state.menu.index("Help")
        keep_running = self.m.handle_key(DummyStdScr(), state, ord("r"))
        self.assertTrue(keep_running)
        self.assertIsNotNone(state.viewer)
        self.assertEqual(state.viewer.title if state.viewer is not None else None, "README.md")

    def test_stamp_enter_opens_inline_review_panel_without_prompt_ladder(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Stamp")

        with mock.patch.object(m, "_prompt_str_curses", side_effect=AssertionError("stamp Enter should not prompt immediately")), mock.patch.object(
            m, "_prompt_bool_curses", side_effect=AssertionError("stamp Enter should not prompt immediately")
        ), mock.patch.object(m, "stamp_pack", side_effect=AssertionError("stamp Enter should not execute immediately")):
            keep_running = m.handle_key(DummyStdScr(), state, m.curses.KEY_ENTER)

        self.assertTrue(keep_running)
        self.assertIsNone(state.viewer)
        self.assertIsNotNone(state.stamp_panel_draft)
        self.assertIn("review", state.status.lower())
        self.assertEqual(state.log_lines, [])

    def test_stamp_panel_shortcuts_stay_in_panel_without_prompt_ladder(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Stamp")

        keep_running = m.handle_key(DummyStdScr(), state, ord("i"))

        self.assertTrue(keep_running)
        self.assertIsNone(state.viewer)
        self.assertIsNotNone(state.stamp_panel_draft)
        self.assertIn("input", state.status.lower())

    def test_stamp_panel_confirm_runs_stamp_from_config(self) -> None:
        m = self.m
        state = self._state()
        state.selected = state.menu.index("Stamp")

        keep_running = m.handle_key(DummyStdScr(), state, m.curses.KEY_ENTER)
        self.assertTrue(keep_running)
        self.assertIsNotNone(state.stamp_panel_draft)
        state.stamp_panel_selected = len(m._stamp_panel_rows(state)) - 1

        with mock.patch.object(m, "_run_stamp_from_config") as run_from_config:
            keep_running = m.handle_key(DummyStdScr(), state, m.curses.KEY_ENTER)

        self.assertTrue(keep_running)
        run_from_config.assert_called_once()
        self.assertIsNone(state.stamp_panel_draft)

    def test_audio_player_command_supports_linux_stub_backend_selection(self) -> None:
        m = self.m
        wav_path = ROOT / "tests" / "fixtures" / "dummy.wav"
        with mock.patch.object(m.sys, "platform", "linux"), mock.patch.object(
            m.shutil,
            "which",
            side_effect=lambda name: "/usr/bin/paplay" if name == "paplay" else None,
        ):
            self.assertEqual(m._audio_player_command(wav_path), ["paplay", str(wav_path)])

        with mock.patch.object(m.sys, "platform", "linux"), mock.patch.object(
            m.shutil,
            "which",
            side_effect=lambda name: "/usr/bin/aplay" if name == "aplay" else None,
        ):
            self.assertEqual(m._audio_player_command(wav_path), ["aplay", "-q", str(wav_path)])


if __name__ == "__main__":
    unittest.main()
