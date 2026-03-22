from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from eps import cli


ROOT = Path(__file__).resolve().parents[1]


def _load_eps_tui_module():
    spec = importlib.util.spec_from_file_location("eps_tui_experience_contract", ROOT / "bin" / "eps.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestTuiExperienceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load_eps_tui_module()

    def test_menu_leads_with_quickstart_and_experience_mode(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        self.assertEqual(state.menu[:2], ["90-Second Start", "Experience Mode"])

    def test_quickstart_preview_prioritizes_stamp_then_verify_and_machine_path(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        preview = m._selection_preview(state, "90-Second Start", width=200, height=40)
        joined = "\n".join(preview)
        self.assertIn("python3 -m eps stamp", joined)
        self.assertIn("python3 -m eps verify", joined)
        self.assertIn("stamp-bin --json", joined)
        self.assertIn("not a CSPRNG replacement", joined)

    def test_experience_preview_describes_calm_and_noisy_without_entropy_claims(self) -> None:
        m = self.m
        state = m.AppState(theme=m.Theme(normal=0, reverse=0, header=0))
        preview = m._selection_preview(state, "Experience Mode", width=200, height=40)
        joined = "\n".join(preview)
        self.assertIn("Current profile: Calm", joined)
        self.assertIn("Calm -> quiet guidance", joined)
        self.assertIn("Noisy -> optional ceremony cues", joined)
        self.assertIn("does not improve entropy quality", joined)

    def test_cli_help_mentions_first_success_path_and_machine_mode(self) -> None:
        help_text = cli.build_parser().format_help()
        self.assertIn("eps stamp --input /ABS/PATH/TO/DIR --out ./out --zip", help_text)
        self.assertIn("eps verify --pack ./out/<pack_root_sha256>", help_text)
        self.assertIn("eps stamp-bin --json", help_text)
        self.assertIn("not RNG", help_text)


if __name__ == "__main__":
    unittest.main()
