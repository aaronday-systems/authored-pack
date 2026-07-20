from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import load_tui_module


class DummyStdScr:
    def __init__(self, inputs: list[object] | None = None) -> None:
        self.inputs = list(inputs or [])

    def getmaxyx(self) -> tuple[int, int]:
        return (24, 80)

    def move(self, *_args, **_kwargs) -> None:
        return None

    def clrtoeol(self) -> None:
        return None

    def refresh(self) -> None:
        return None

    def addstr(self, *_args, **_kwargs) -> None:
        return None

    def getch(self) -> int:
        value = self.inputs.pop(0) if self.inputs else -1
        return ord(value) if isinstance(value, str) else int(value)

    def get_wch(self):
        return self.inputs.pop(0) if self.inputs else -1

    def nodelay(self, *_args, **_kwargs) -> None:
        return None

    def timeout(self, *_args, **_kwargs) -> None:
        return None


class ImmediateThread:
    def __init__(self, *, target, **_kwargs) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


class TestTuiOperatorSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = load_tui_module("authored_pack_tui_operator_safety")

    def _state(self):
        return self.m.AppState(theme=self.m.Theme(normal=0, reverse=0, header=0))

    def _source(self, i: int):
        raw = f"source-{i}".encode("utf-8")
        return self.m.AuthoredSource(
            kind="text",
            name=f"source-{i}.txt",
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            text=raw.decode("utf-8"),
        )

    def test_80x24_advanced_review_keeps_selected_row_and_confirm_visible(self) -> None:
        state = self._state()
        state.authored_sources = [self._source(i) for i in range(8)]
        state.assemble_config.input_mode = "sources"
        state.assemble_config.derive_seed = True
        self.m._open_assemble_panel(state, "write_sources", show_advanced=True)

        lines = self.m._assemble_panel_lines(state, width=54, height=20)

        self.assertTrue(any(line.startswith("> ") and "Include source record" in line for line in lines))
        self.assertTrue(any("Assemble now" in line for line in lines))

    def test_large_source_list_follows_selection_and_delete_targets_visible_row(self) -> None:
        state = self._state()
        state.authored_sources = [self._source(i) for i in range(30)]
        state.entropy_selected = 25
        state.focus = "entropy"
        self.m._select_screen(state, self.m.SCREEN_SOURCES)

        lines = self.m._authored_sources_preview(state, width=54, height=20)
        self.assertTrue(any(">> [text] source-25.txt" in line for line in lines))

        self.assertTrue(self.m.handle_key(DummyStdScr(), state, ord("D")))
        self.assertNotIn("source-25.txt", [source.name for source in state.authored_sources])

    def test_destructive_source_keys_require_list_focus(self) -> None:
        state = self._state()
        state.authored_sources = [self._source(1), self._source(2)]
        state.focus = "menu"
        self.m._select_screen(state, self.m.SCREEN_SOURCES)

        with mock.patch.object(self.m, "_prompt_bool_curses") as prompt:
            self.assertTrue(self.m.handle_key(DummyStdScr(), state, ord("D")))
            self.assertTrue(self.m.handle_key(DummyStdScr(), state, ord("C")))

        self.assertEqual(len(state.authored_sources), 2)
        prompt.assert_not_called()

    def test_clear_and_quit_are_guarded_when_work_would_be_lost(self) -> None:
        state = self._state()
        state.authored_sources = [self._source(1)]
        state.focus = "entropy"
        self.m._select_screen(state, self.m.SCREEN_SOURCES)

        with mock.patch.object(self.m, "_prompt_bool_curses", return_value=False) as prompt:
            self.assertTrue(self.m.handle_key(DummyStdScr(), state, ord("C")))
        self.assertEqual(len(state.authored_sources), 1)
        self.assertIn("Clear all", prompt.call_args.args[1])

        with mock.patch.object(self.m, "_prompt_bool_curses", return_value=False) as prompt:
            self.assertTrue(self.m.handle_key(DummyStdScr(), state, ord("q")))
        self.assertIn("Quit", prompt.call_args.args[1])

        with mock.patch.object(self.m, "_prompt_bool_curses", return_value=True):
            self.assertFalse(self.m.handle_key(DummyStdScr(), state, ord("q")))

    def test_prompt_accepts_wide_character_input(self) -> None:
        value = self.m._prompt_str_curses(DummyStdScr(["M", "ü", "n", "c", "h", "e", "n", "\n"]), "note")
        self.assertEqual(value, "München")

    def test_unknown_user_path_becomes_terminal_visible_error(self) -> None:
        actions = self.m._prepare_drop_actions(["~authored_pack_user_that_cannot_exist/item.txt"])
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0].terminal)
        self.assertFalse(actions[0].success)
        self.assertIn("path expansion failed", actions[0].message.lower())

    def test_oversized_text_import_is_rejected_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.txt"
            path.write_bytes(b"x" * 2_000_001)

            actions = self.m._prepare_drop_actions([str(path)], apply_mode="sources")

        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0].terminal)
        self.assertIsNone(actions[0].source)
        self.assertIn("exceeds", actions[0].message.lower())

    def test_watched_drop_overflow_is_deferred_not_marked_seen(self) -> None:
        state = self._state()
        with tempfile.TemporaryDirectory() as tmp:
            state.drop_dir = Path(tmp)
            for i in range(9):
                (state.drop_dir / f"item-{i}.txt").write_text(str(i), encoding="utf-8")
            queued: list[tuple[list[str], list[str]]] = []

            def capture(_state, paths, *, seen_keys, **_kwargs):
                queued.append((list(paths), list(seen_keys)))

            with mock.patch.object(self.m, "_queue_drop_paths", side_effect=capture):
                self.m._poll_drop_dir(state)
                first_deferred = set(str(path.resolve()) for path in sorted(state.drop_dir.iterdir())[7:])
                self.assertEqual(state.drop_deferred_count, 2)
                self.assertTrue(first_deferred.isdisjoint(state.drop_seen))
                self.assertTrue(first_deferred.isdisjoint(state.drop_pending_seen))
                state.drop_seen.update(queued[0][1])
                self.m._poll_drop_dir(state)

        self.assertEqual([len(batch[0]) for batch in queued], [7, 2])

    def test_worker_failure_releases_busy_state_when_result_is_applied(self) -> None:
        state = self._state()
        state.drop_pending_requests.append(
            self.m.DropBatchRequest(paths=["bad"], seen_keys=["bad-key"], play_sfx=False)
        )
        with mock.patch.object(self.m, "_prepare_drop_actions", side_effect=RuntimeError("boom")), mock.patch.object(
            self.m.threading, "Thread", ImmediateThread
        ):
            self.m._start_drop_worker_if_idle(state)

        self.assertTrue(state.drop_worker_busy)
        self.m._drain_drop_results(state)
        self.assertFalse(state.drop_worker_busy)
        self.assertTrue(any("boom" in line for line in state.drop_last_msgs))

    def test_completed_drop_result_blocks_next_worker_until_applied(self) -> None:
        state = self._state()
        state.drop_pending_requests.extend(
            [
                self.m.DropBatchRequest(paths=[name], seen_keys=[name], play_sfx=False)
                for name in ("first", "second")
            ]
        )
        calls: list[str] = []

        def prepare(paths, **_kwargs):
            calls.append(paths[0])
            return [self.m.DropPreparedAction(message=f"Not usable: {paths[0]}", terminal=True)]

        with mock.patch.object(self.m, "_prepare_drop_actions", side_effect=prepare), mock.patch.object(
            self.m.threading, "Thread", ImmediateThread
        ):
            self.m._start_drop_worker_if_idle(state)
            self.m._start_drop_worker_if_idle(state)
            self.assertEqual(calls, ["first"])
            self.assertTrue(state.drop_worker_busy)

            self.m._drain_drop_results(state)
            self.assertEqual(calls, ["first", "second"])
            self.assertTrue(state.drop_worker_busy)
            self.m._drain_drop_results(state)

        self.assertFalse(state.drop_worker_busy)

    def test_enter_dismisses_viewer_where_advertised(self) -> None:
        state = self._state()
        self.m.open_viewer(state, "test", ["Press Enter to dismiss."])
        self.assertTrue(self.m.handle_key(DummyStdScr(), state, 10))
        self.assertIsNone(state.viewer)

    def test_optional_metadata_can_be_cleared_explicitly(self) -> None:
        state = self._state()
        state.assemble_config.notes = "remove me"
        self.m._open_assemble_panel(state, "notes", show_advanced=True)

        self.m._edit_assemble_text_row(state, DummyStdScr([4]), "notes")

        self.assertEqual(state.assemble_panel_draft.notes, "")

    def test_parent_candidate_race_is_reported(self) -> None:
        state = self._state()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            for name in ("a", "b"):
                candidate = parent / name
                candidate.mkdir()
                (candidate / "manifest.json").write_text("{}", encoding="utf-8")

            with mock.patch.object(self.m, "_candidate_mtime", side_effect=OSError("candidate disappeared")):
                self.m._run_verify_plan(state, DummyStdScr(), pack_s=str(parent), allow_large_manifest=False)

        self.assertEqual(state.status, "Failed.")
        self.assertTrue(any("candidate disappeared" in line for line in state.log_lines))

    def test_multiple_pack_candidates_remain_visible_after_selected_pack_failure(self) -> None:
        state = self._state()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            for name in ("a", "b"):
                candidate = parent / name
                candidate.mkdir()
                (candidate / "manifest.json").write_text("{}", encoding="utf-8")

            self.m._run_verify_plan(state, DummyStdScr(), pack_s=str(parent), allow_large_manifest=False)

        self.assertEqual(state.status, "Failed.")
        self.assertIn("candidate packs found: 2", state.log_lines)
