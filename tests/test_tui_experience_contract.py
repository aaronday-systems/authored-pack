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
        self.assertIn("Choose the path that matches what you already have.", joined)
        self.assertIn("Pack a Folder You Already Have", joined)
        self.assertIn("Build a Pack from Collected Sources", joined)
        self.assertIn("Check a Pack You Already Assembled", joined)
        self.assertIn("Enter = choose a folder", joined)
        self.assertNotIn("machine-sidecar route", joined)

    def test_sources_card_prioritizes_empty_state_and_collection_actions(self) -> None:
        joined = self._preview("Sources")
        self.assertIn("AUTHORED SOURCES // collect what you want in the pack", joined)
        self.assertIn("Collected: 0 sources", joined)
        self.assertIn("Kinds: photo 0  text 0  tap 0", joined)
        self.assertIn("P import files/folders", joined)
        self.assertIn("Add a photo, text note, or tap sample.", joined)
        self.assertIn("ignore this screen", joined.lower())
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
        summary_line = next(line for line in joined.splitlines() if line.startswith("Kinds:"))

        self.assertIn("Collected: 3 sources", joined)
        self.assertIn("Kinds: photo 1  text 1  tap 1", joined)
        self.assertTrue(all(ord(ch) < 128 for ch in summary_line))

    def test_stamp_card_summarizes_review_before_advanced_prompts(self) -> None:
        joined = self._preview("Stamp")
        self.assertIn("- What to pack: not set", joined)
        self.assertIn("- Save in: out", joined)
        self.assertIn("Next: press I to choose what to pack", joined)
        self.assertIn("Enter = review and assemble", joined)
        self.assertIn("Toggle: Authored Sources / Derive seed / Zip copy / Evidence zip", joined)
        self.assertIn("Writes a deterministic pack folder and optional zip export.", joined)
        self.assertNotIn("prompt ladder", joined)

    def test_verify_card_focuses_on_integrity_audit(self) -> None:
        joined = self._preview("Verify")
        self.assertIn("Best target:", joined)
        self.assertIn("out/<pack_root_sha256>/", joined)
        self.assertIn("Checks pack root, payload hashes, and receipt consistency.", joined)
        self.assertIn("Enter = verify this path", joined)
        self.assertIn("- pack: not set", joined)
        self.assertIn("check an assembled pack", joined.lower())
        self.assertIn("authored_pack.zip", joined)
        self.assertIn("newest pack inside it", joined)

    def test_help_card_is_curated_not_raw_docs(self) -> None:
        joined = self._preview("Help")
        self.assertIn("for humans", joined)
        self.assertIn("most common path", joined)
        self.assertIn("workflow", joined)
        self.assertIn("folder  -> Start   -> Assemble -> Verify", joined)
        self.assertIn("sources -> Sources -> Assemble -> Verify", joined)
        self.assertIn("key actions", joined)
        self.assertIn("Press Down for Start", joined)
        self.assertIn("Down = begin", joined)
        self.assertIn("trust boundary", joined)
        self.assertIn("authored_pack.zip", joined)
        self.assertIn("more detail", joined)
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
