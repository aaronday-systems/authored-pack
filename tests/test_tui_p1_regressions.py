from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.support import load_tui_module


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


class TestTuiP1Regressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = load_tui_module("authored_pack_tui_p1_regressions")

    def test_build_sources_payload_dir_fails_closed_on_photo_drift(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            photo = tmp_path / "photo.jpg"
            photo.write_bytes(b"abc")
            src = m.AuthoredSource(
                kind="photo",
                name="photo.jpg",
                sha256=hashlib.sha256(b"abc").hexdigest(),
                size_bytes=3,
                path=photo,
            )
            photo.write_bytes(b"abcd")

            with self.assertRaises(ValueError):
                m._build_sources_payload_dir([src])

    def test_run_assemble_from_config_allows_authored_sources_with_one_source(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "out"
            out_dir.mkdir()

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.authored_sources.append(
                m.AuthoredSource(kind="text", name="note", sha256=hashlib.sha256(b"hello").hexdigest(), size_bytes=5, text="hello")
            )
            state.assemble_config.input_mode = "sources"
            state.assemble_config.out_path = str(out_dir)
            state.assemble_config.derive_seed = False

            m._run_assemble_from_config(state, DummyStdScr())

            self.assertEqual(state.status, "Done.")
            self.assertIsNotNone(state.last_pack_dir)
            self.assertTrue(any(line == "RESULT: pack written successfully." for line in state.log_lines))

    def test_run_assemble_from_config_blocks_mix_sources_until_seven_ready(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "out"
            out_dir.mkdir()

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            for idx in range(3):
                state.authored_sources.append(
                    m.AuthoredSource(
                        kind="text",
                        name=f"note{idx}",
                        sha256=hashlib.sha256(f"note{idx}".encode("utf-8")).hexdigest(),
                        size_bytes=5,
                        text=f"note{idx}",
                    )
                )
            state.assemble_config.input_mode = "sources"
            state.assemble_config.out_path = str(out_dir)
            state.assemble_config.derive_seed = True
            state.assemble_config.mix_sources = True

            m._run_assemble_from_config(state, DummyStdScr())

            self.assertEqual(state.status, "Failed.")
            self.assertEqual(state.log_lines[0], "Assemble blocked: staged sources are not ready to mix into the seed.")
            self.assertTrue(any("Sources ready for seed: 3/7" in line for line in state.log_lines))
            self.assertTrue(any("Need 4 more collected sources to use this option." in line for line in state.log_lines))

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

            orig_prepare = m._prepare_drop_actions

            def fake_prepare_drop_actions(paths, *, seen_keys=None, apply_mode="auto", max_apply=None):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return [
                        m.DropPreparedAction(
                            message=f"Text add failed: {paths[0]}: busy",
                            success=False,
                            terminal=False,
                            seen_key=seen_keys[0] if seen_keys else None,
                        )
                    ]
                return [
                    m.DropPreparedAction(
                        message=f"Text source added: {Path(paths[0]).name}",
                        success=True,
                        seen_key=seen_keys[0] if seen_keys else None,
                        source=m.AuthoredSource(kind="text", name="landed.txt", sha256="a" * 64, size_bytes=5, text="hello"),
                    )
                ]

            try:
                m._prepare_drop_actions = fake_prepare_drop_actions
                m._poll_drop_dir(state)
                m._drain_drop_results(state)
                self.assertEqual(state.drop_seen, set())
                m._poll_drop_dir(state)
                m._drain_drop_results(state)
            finally:
                m._prepare_drop_actions = orig_prepare

            self.assertTrue(any("landed.txt" in msg for msg in state.drop_last_msgs))
            self.assertEqual(len(state.drop_seen), 1)

    def test_poll_drop_dir_does_not_requeue_pending_item_while_worker_busy(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            drop_dir = tmp_path / "drop"
            drop_dir.mkdir()
            (drop_dir / "landed.txt").write_text("hello", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.drop_dir = drop_dir

            m._poll_drop_dir(state)
            self.assertLessEqual(len(state.drop_pending_requests) + int(state.drop_worker_busy), 1)
            self.assertEqual(len(state.drop_pending_seen), 1)
            m._poll_drop_dir(state)

            self.assertLessEqual(len(state.drop_pending_requests) + int(state.drop_worker_busy), 1)
            self.assertEqual(len(state.drop_pending_seen), 1)

    def test_poll_drop_dir_marks_terminal_rejections_seen(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            drop_dir = tmp_path / "drop"
            drop_dir.mkdir()
            (drop_dir / "bad.txt").write_text("nope", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.drop_dir = drop_dir
            call_count = {"n": 0}

            orig_prepare = m._prepare_drop_actions

            def fake_prepare_drop_actions(paths, *, seen_keys=None, apply_mode="auto", max_apply=None):
                call_count["n"] += 1
                return [
                    m.DropPreparedAction(
                        message=f"Not usable: {paths[0]}",
                        success=False,
                        terminal=True,
                        seen_key=seen_keys[0] if seen_keys else None,
                    )
                ]

            try:
                m._prepare_drop_actions = fake_prepare_drop_actions
                m._poll_drop_dir(state)
                m._drain_drop_results(state)
                m._poll_drop_dir(state)
                m._drain_drop_results(state)
            finally:
                m._prepare_drop_actions = orig_prepare

            self.assertEqual(call_count["n"], 1)
            self.assertEqual(len(state.drop_seen), 1)
            self.assertTrue(any("bad.txt" in msg for msg in state.drop_last_msgs))

    def test_prompt_str_curses_returns_none_on_escape(self) -> None:
        m = self.m
        stdscr = DummyStdScr(inputs=[27])
        self.assertIsNone(m._prompt_str_curses(stdscr, "(Authored Pack) path", default="."))

    def test_run_assemble_from_config_accepts_finder_escaped_folder_path(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "images 2025"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            out_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.assemble_config.input_mode = "folder"
            state.assemble_config.input_path = str(input_dir).replace(" ", "\\ ")
            state.assemble_config.out_path = str(out_dir)

            m._run_assemble_from_config(state, DummyStdScr())

            self.assertEqual(state.status, "Done.")
            self.assertIsNotNone(state.last_pack_dir)
            self.assertTrue(state.last_pack_dir is not None and (state.last_pack_dir / "manifest.json").is_file())

    def test_split_drop_payload_keeps_existing_unescaped_path_with_spaces_intact(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "images 2025"
            input_dir.mkdir()

            self.assertEqual(m._split_drop_payload(str(input_dir)), [str(input_dir)])

    def test_close_seed_viewer_preserves_result_status(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        state.status = "Done."
        state.log_lines = ["pack_dir: /tmp/out"]
        m._show_seed_reveal(state, b"\x01" * 32)

        m.close_viewer(state)

        self.assertEqual(state.status, "Done.")
        self.assertIsNone(state.viewer)

    def test_authored_sources_menu_navigation_stays_on_menu_until_explicit_focus(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        state.selected = state.menu.index("Sources")
        state.focus = "menu"

        keep_running = m.handle_key(DummyStdScr(), state, m.curses.KEY_DOWN)

        self.assertTrue(keep_running)
        self.assertEqual(state.focus, "menu")
        self.assertEqual(state.menu[state.selected], "Assemble")

    def test_tab_on_empty_authored_sources_keeps_menu_focus(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        state.selected = state.menu.index("Sources")
        state.focus = "menu"

        keep_running = m.handle_key(DummyStdScr(), state, 9)

        self.assertTrue(keep_running)
        self.assertEqual(state.focus, "menu")

    def test_verify_from_config_logs_verified_path(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "out" / ("a" * 64)
            pack_dir.mkdir(parents=True)
            (pack_dir / "manifest.json").write_text("{}", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.verify_config.pack_path = str(tmp_path / "out")
            orig_verify_pack = m.verify_pack

            def fake_verify_pack(_pack, **_kwargs):
                return SimpleNamespace(
                    ok=True,
                    root_sha256="a" * 64,
                    payload_root_sha256="b" * 64,
                    file_count=3,
                    total_bytes=42,
                    errors=[],
                )

            try:
                m.verify_pack = fake_verify_pack
                m._run_verify_from_config(state, DummyStdScr())
            finally:
                m.verify_pack = orig_verify_pack

            self.assertEqual(state.status, "Done.")
            self.assertTrue(any(line.startswith("verified_path: ") for line in state.log_lines))
            self.assertTrue(any("used most recent pack in that folder" in line.lower() for line in state.log_lines))

    def test_effective_verify_path_preserves_missing_hash_dir_request(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            missing = out_dir / ("a" * 64)

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.verify_config.pack_path = str(missing)

            self.assertEqual(m._effective_verify_path(state), str(missing))

    def test_run_verify_plan_reports_missing_pack_path_cleanly(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            missing = tmp_path / "missing-pack"
            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))

            m._run_verify_plan(state, DummyStdScr(), pack_s=str(missing), allow_large_manifest=False)

            self.assertEqual(state.status, "Failed.")
            self.assertEqual(state.log_lines[0], "VERIFY RESULT // failed")
            self.assertTrue(any(line.startswith("verify_target: ") for line in state.log_lines))
            self.assertTrue(any("pack path not found:" in line for line in state.log_lines))
            self.assertFalse(any("unsupported pack path" in line for line in state.log_lines))

    def test_run_verify_plan_surfaces_ds_store_hints(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / ("a" * 64)
            pack_dir.mkdir()
            (pack_dir / "manifest.json").write_text("{}", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            orig_verify_pack = m.verify_pack

            def fake_verify_pack(_pack, **_kwargs):
                return SimpleNamespace(
                    ok=False,
                    root_sha256="",
                    payload_root_sha256="",
                    file_count=0,
                    total_bytes=0,
                    errors=["unexpected payload files present: payload/.DS_Store"],
                )

            try:
                m.verify_pack = fake_verify_pack
                m._run_verify_plan(state, DummyStdScr(), pack_s=str(pack_dir), allow_large_manifest=False)
            finally:
                m.verify_pack = orig_verify_pack

            self.assertEqual(state.status, "Failed.")
            self.assertTrue(any(line.startswith("verify_target: ") for line in state.log_lines))
            self.assertTrue(any("macOS added .DS_Store" in line for line in state.log_lines))
            self.assertTrue(any("Try authored_pack.zip" in line for line in state.log_lines))
            self.assertTrue(any("Press P to choose another pack path." in line for line in state.log_lines))

    def test_failed_verify_of_plain_directory_does_not_poison_remembered_pack_path(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bad_dir = tmp_path / "not-a-pack"
            bad_dir.mkdir()
            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.verify_config.pack_path = ""

            m._run_verify_plan(state, DummyStdScr(), pack_s=str(bad_dir), allow_large_manifest=False)

            self.assertEqual(state.status, "Failed.")
            self.assertEqual(state.verify_config.pack_path, "")
            self.assertIsNone(state.last_pack_dir)

    def test_noisy_mode_does_not_block_folder_assemble_without_staged_sources(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            out_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0), insane=True)
            state.assemble_config.input_mode = "folder"
            state.assemble_config.input_path = str(input_dir)
            state.assemble_config.out_path = str(out_dir)
            orig_assemble_pack = m.assemble_pack
            orig_stamp_with_fx = m._assemble_with_insane_fx

            def fake_assemble_pack(**_kwargs):
                pack_dir = out_dir / ("a" * 64)
                pack_dir.mkdir(parents=True, exist_ok=True)
                return SimpleNamespace(
                    pack_dir=pack_dir,
                    root_sha256="a" * 64,
                    pack_root_sha256="a" * 64,
                    payload_root_sha256="b" * 64,
                    receipt={},
                    seed_master=None,
                    zip_path=None,
                    evidence_bundle_path=None,
                    evidence_bundle_sha256=None,
                )

            def fake_assemble_with_fx(_stdscr, _state, do_assemble, **_kwargs):
                return do_assemble()

            try:
                m.assemble_pack = fake_assemble_pack
                m._assemble_with_insane_fx = fake_assemble_with_fx
                m._run_assemble_from_config(state, DummyStdScr())
            finally:
                m.assemble_pack = orig_assemble_pack
                m._assemble_with_insane_fx = orig_stamp_with_fx

            self.assertEqual(state.status, "Done.")
            self.assertTrue(any(line == "RESULT: pack written successfully." for line in state.log_lines))

    def test_noisy_assemble_failure_triggers_failure_fx(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0), insane=True)
        state.palette = m.InsanePalette(bg=[0], header=[0], menu_hot=[0], menu_dim=0, divider=0, text=0, ok=0, warn=0, info=0)

        def fail_assemble():
            raise ValueError("boom")

        with mock.patch.object(m, "_start_supernova_sfx_best_effort"), mock.patch.object(m, "_fx_kaleidoscope"), mock.patch.object(
            m, "_fx_assemble_failure"
        ) as fx_fail:
            with self.assertRaisesRegex(ValueError, "boom"):
                m._assemble_with_insane_fx(DummyStdScr(), state, fail_assemble, min_assembling_s=0.0, created_s=0.0)

        fx_fail.assert_called_once()

    def test_verify_config_edits_clear_old_log_lines(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        state.log_lines = ["VERIFY RESULT // checked", "RESULT: pack is self-consistent."]

        with mock.patch.object(m, "_prompt_str_curses", return_value="/tmp/example.pack"):
            self.assertTrue(m._edit_verify_path(state, DummyStdScr()))

        self.assertEqual(state.log_lines, [])

    def test_audit_writer_skips_unwritable_entries_and_stays_transactional(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            good_text = m.AuthoredSource(
                kind="text",
                name="note",
                sha256=hashlib.sha256(b"hello").hexdigest(),
                size_bytes=5,
                text="hello",
            )
            missing_photo = m.AuthoredSource(
                kind="photo",
                name="missing.jpg",
                sha256="b" * 64,
                size_bytes=123,
                path=tmp_path / "missing.jpg",
            )

            audit_dir, warnings, materialized_count = m._write_authored_sources_into_pack(tmp_path, [good_text, missing_photo])

            self.assertIsNotNone(audit_dir)
            self.assertTrue(audit_dir is not None and audit_dir.is_dir())
            self.assertTrue(warnings)
            self.assertEqual(materialized_count, 1)
            index_path = audit_dir / "sources.index.json"
            self.assertTrue(index_path.is_file())
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["kind"], "text")
            self.assertEqual(payload[0]["path"], "001_note.txt")
            self.assertTrue(any("missing.jpg" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
