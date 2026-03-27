from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_authored_pack_tui_module():
    spec = importlib.util.spec_from_file_location("authored_pack_tui_p1_regressions", ROOT / "bin" / "authored_pack.py")
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


class TestTuiP1Regressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load_authored_pack_tui_module()

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

    def test_action_stamp_cleans_tmp_payload_dir_when_sources_materialization_fails(self) -> None:
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

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.authored_sources.append(src)

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

    def test_action_stamp_uses_one_shot_seed_reveal_and_no_persistent_seed_logs(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            out_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.authored_sources.append(
                m.AuthoredSource(kind="text", name="note", sha256=hashlib.sha256(b"note").hexdigest(), size_bytes=4, text="note")
            )

            prompts_s = iter([str(input_dir), str(out_dir), "", "", ""])
            bool_answers = iter([False, False, False, True, False, False, True, False, False])
            bool_prompts: list[str] = []

            orig_prompt_str = m._prompt_str_curses
            orig_prompt_bool = m._prompt_bool_curses
            orig_stamp_pack = m.stamp_pack

            def fake_prompt_str(*_args, **_kwargs) -> str:
                return next(prompts_s)

            def fake_prompt_bool(_stdscr, label: str, *, default: bool = False) -> bool:
                bool_prompts.append(label)
                return next(bool_answers)

            def fake_stamp_pack(**_kwargs):
                pack_dir = out_dir / ("a" * 64)
                pack_dir.mkdir(parents=True, exist_ok=True)
                receipt = {"derived_seed_fingerprint_sha256": "f" * 64}
                return SimpleNamespace(
                    pack_dir=pack_dir,
                    root_sha256="a" * 64,
                    pack_root_sha256="a" * 64,
                    payload_root_sha256="b" * 64,
                    receipt=receipt,
                    seed_master=b"\x01" * 32,
                    zip_path=None,
                    evidence_bundle_path=None,
                    evidence_bundle_sha256=None,
                )

            try:
                m._prompt_str_curses = fake_prompt_str
                m._prompt_bool_curses = fake_prompt_bool
                m.stamp_pack = fake_stamp_pack
                m._action_stamp(state, DummyStdScr())
            finally:
                m._prompt_str_curses = orig_prompt_str
                m._prompt_bool_curses = orig_prompt_bool
                m.stamp_pack = orig_stamp_pack

            self.assertIsNotNone(state.viewer)
            self.assertEqual(state.viewer.title if state.viewer is not None else None, "Derived Seed Material")
            self.assertTrue(state.viewer is not None and any("derived_seed.hex" in line for line in state.viewer.lines))
            self.assertFalse(any("derived_seed.hex" in line for line in state.log_lines))
            self.assertFalse(any("derived_seed.b64" in line for line in state.log_lines))
            self.assertIn("(Authored Pack) derive seed", bool_prompts)
            self.assertIn("(Authored Pack) write authored sources audit into pack", bool_prompts)
            self.assertFalse(any("write entropy source audit into pack" in label for label in bool_prompts))
            self.assertFalse(any("LOCKDOWN" in label for label in bool_prompts))
            self.assertTrue(any("Seed path: root-only seed" in line for line in state.log_lines))
            self.assertTrue(any("staged authored sources do not affect the root-only seed" in line for line in state.log_lines))

    def test_action_stamp_finalizes_receipt_before_zip_and_evidence(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            out_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            for idx in range(7):
                state.authored_sources.append(
                    m.AuthoredSource(
                        kind="text",
                        name=f"note{idx}",
                        sha256=hashlib.sha256(f"note{idx}".encode("utf-8")).hexdigest(),
                        size_bytes=5,
                        text=f"note{idx}",
                    )
                )
            missing_photo = m.AuthoredSource(
                kind="photo",
                name="missing.jpg",
                sha256=hashlib.sha256(b"missing.jpg").hexdigest(),
                size_bytes=123,
                path=tmp_path / "missing.jpg",
            )
            state.authored_sources.append(missing_photo)

            prompts_s = iter([str(input_dir), str(out_dir), "", "", ""])
            bool_answers = iter([False, False, True, True, True, False, False, True, True])

            orig_prompt_str = m._prompt_str_curses
            orig_prompt_bool = m._prompt_bool_curses
            orig_stamp_pack = m.stamp_pack
            receipt_snapshots: list[dict[str, object]] = []

            def fake_prompt_str(*_args, **_kwargs) -> str:
                return next(prompts_s)

            def fake_prompt_bool(*_args, **_kwargs) -> bool:
                return next(bool_answers)

            def fake_stamp_pack(**_kwargs):
                pack_dir = tmp_path / "pack"
                pack_dir.mkdir(parents=True, exist_ok=True)
                before_finalize = _kwargs.get("before_finalize")
                extra = before_finalize(pack_dir) if before_finalize is not None else {}
                receipt = {"derived_seed_fingerprint_sha256": "f" * 64, "zip_path": "authored_pack.zip"}
                receipt.update(extra or {})
                (pack_dir / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                receipt_snapshots.append(json.loads((pack_dir / "receipt.json").read_text(encoding="utf-8")))
                zip_path = pack_dir / "authored_pack.zip"
                zip_path.write_bytes(b"zip")
                evidence_path = pack_dir / "authored_evidence_a.zip"
                evidence_path.write_bytes(b"bundle")
                receipt_snapshots.append(json.loads((pack_dir / "receipt.json").read_text(encoding="utf-8")))
                return SimpleNamespace(
                    pack_dir=pack_dir,
                    root_sha256="a" * 64,
                    pack_root_sha256="a" * 64,
                    payload_root_sha256="b" * 64,
                    receipt=receipt,
                    seed_master=b"\x02" * 32,
                    zip_path=zip_path,
                    evidence_bundle_path=evidence_path,
                    evidence_bundle_sha256="e" * 64,
                )

            try:
                m._prompt_str_curses = fake_prompt_str
                m._prompt_bool_curses = fake_prompt_bool
                m.stamp_pack = fake_stamp_pack
                m._action_stamp(state, DummyStdScr())
            finally:
                m._prompt_str_curses = orig_prompt_str
                m._prompt_bool_curses = orig_prompt_bool
                m.stamp_pack = orig_stamp_pack

            receipt_path = tmp_path / "pack" / "receipt.json"
            self.assertTrue(receipt_path.is_file())
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["authored_sources_audit_status"], "partial")
            self.assertEqual(receipt["authored_sources_audit_requested_count"], 8)
            self.assertEqual(receipt["authored_sources_audit_materialized_count"], 7)
            self.assertTrue(receipt["authored_sources_audit_warnings"])
            self.assertTrue(any("missing.jpg" in w for w in receipt["authored_sources_audit_warnings"]))
            self.assertEqual(receipt["zip_path"], "authored_pack.zip")
            self.assertNotIn("evidence_bundle_path", receipt)
            self.assertNotIn("evidence_bundle_sha256", receipt)
            self.assertTrue(receipt_snapshots)
            self.assertTrue(all(snapshot == receipt for snapshot in receipt_snapshots))
            self.assertEqual(state.status, "Done.")

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

            def fake_prepare_drop_actions(paths, *, seen_keys=None, max_apply=None):
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

            def fake_prepare_drop_actions(paths, *, seen_keys=None, max_apply=None):
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

    def test_run_stamp_from_config_accepts_finder_escaped_folder_path(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "images 2025"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            out_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
            state.stamp_config.input_mode = "folder"
            state.stamp_config.input_path = str(input_dir).replace(" ", "\\ ")
            state.stamp_config.out_path = str(out_dir)

            m._run_stamp_from_config(state, DummyStdScr())

            self.assertEqual(state.status, "Done.")
            self.assertIsNotNone(state.last_pack_dir)
            self.assertTrue(state.last_pack_dir is not None and (state.last_pack_dir / "manifest.json").is_file())

    def test_authored_sources_menu_navigation_stays_on_menu_until_explicit_focus(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        state.selected = state.menu.index("Sources")
        state.focus = "menu"

        keep_running = m.handle_key(DummyStdScr(), state, m.curses.KEY_DOWN)

        self.assertTrue(keep_running)
        self.assertEqual(state.focus, "menu")
        self.assertEqual(state.menu[state.selected], "Stamp")

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
            self.assertTrue(any("used most recent pack in that folder" == line for line in state.log_lines))

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
            self.assertEqual(state.log_lines[0], "Verify failed.")
            self.assertTrue(any("pack path not found:" in line for line in state.log_lines))
            self.assertFalse(any("unsupported pack path" in line for line in state.log_lines))

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

    def test_noisy_mode_does_not_block_folder_stamp_without_staged_sources(self) -> None:
        m = self.m
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            out_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0), insane=True)
            state.stamp_config.input_mode = "folder"
            state.stamp_config.input_path = str(input_dir)
            state.stamp_config.out_path = str(out_dir)
            orig_stamp_pack = m.stamp_pack
            orig_stamp_with_fx = m._stamp_with_insane_fx

            def fake_stamp_pack(**_kwargs):
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

            def fake_stamp_with_fx(_stdscr, _state, do_stamp, **_kwargs):
                return do_stamp()

            try:
                m.stamp_pack = fake_stamp_pack
                m._stamp_with_insane_fx = fake_stamp_with_fx
                m._run_stamp_from_config(state, DummyStdScr())
            finally:
                m.stamp_pack = orig_stamp_pack
                m._stamp_with_insane_fx = orig_stamp_with_fx

            self.assertEqual(state.status, "Done.")
            self.assertTrue(any(line == "Stamp complete." for line in state.log_lines))

    def test_verify_config_edits_clear_old_log_lines(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        state.log_lines = ["Verify ok."]

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
