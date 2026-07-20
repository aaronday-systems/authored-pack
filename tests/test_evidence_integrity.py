from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import authored_pack.pack as pack_module
from authored_pack.pack import assemble_pack


class TestEvidenceIntegrity(unittest.TestCase):
    def _input(self, root: Path) -> Path:
        input_dir = root / "input"
        input_dir.mkdir()
        (input_dir / "a.txt").write_text("hello", encoding="utf-8")
        return input_dir

    def _sidecar(self, bundle: Path) -> Path:
        return bundle.with_name(bundle.name + ".sha256")

    def _assert_pair_valid(self, bundle: Path) -> None:
        sidecar = self._sidecar(bundle)
        self.assertTrue(bundle.is_file())
        self.assertTrue(sidecar.is_file())
        self.assertEqual(sidecar.read_text(encoding="utf-8"), hashlib.sha256(bundle.read_bytes()).hexdigest() + "\n")
        ok, errors = pack_module._verify_evidence_pair(bundle.parent, bundle, sidecar)
        self.assertTrue(ok, msg=errors)

    def test_missing_or_stale_sidecar_is_repaired_on_reuse(self) -> None:
        for case in ("missing", "stale"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                input_dir = self._input(tmp_path)
                first = assemble_pack(input_dir=input_dir, out_dir=tmp_path / "out", evidence_bundle=True)
                assert first.evidence_bundle_path is not None
                sidecar = self._sidecar(first.evidence_bundle_path)
                if case == "missing":
                    sidecar.unlink()
                else:
                    sidecar.write_text("0" * 64 + "\n", encoding="utf-8")

                reused = assemble_pack(input_dir=input_dir, out_dir=tmp_path / "out", evidence_bundle=True)
                assert reused.evidence_bundle_path is not None
                self._assert_pair_valid(reused.evidence_bundle_path)

    def test_sidecar_write_failure_fails_evidence_operation_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path)
            out = tmp_path / "out"
            real_write = pack_module._safe_write_text

            def fail_sidecar(path: Path, content: str) -> None:
                if path.name.endswith(".zip.sha256"):
                    raise OSError("sidecar write failed")
                real_write(path, content)

            with patch.object(pack_module, "_safe_write_text", side_effect=fail_sidecar):
                with self.assertRaisesRegex(OSError, "sidecar write failed"):
                    assemble_pack(input_dir=input_dir, out_dir=out, evidence_bundle=True)
            self.assertEqual(list(out.iterdir()), [])

    def test_stale_embedded_receipt_is_regenerated_on_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path)
            out = tmp_path / "out"
            first = assemble_pack(input_dir=input_dir, out_dir=out, evidence_bundle=True)
            assert first.evidence_bundle_path is not None
            bundle = first.evidence_bundle_path
            with zipfile.ZipFile(bundle, "r") as source:
                members = {name: source.read(name) for name in source.namelist()}
            stale_receipt = json.loads(members["receipt.json"].decode("utf-8"))
            stale_receipt["operator_note"] = "stale"
            members["receipt.json"] = (json.dumps(stale_receipt, sort_keys=True) + "\n").encode("utf-8")
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as target:
                for name, payload in members.items():
                    target.writestr(name, payload)
            self._sidecar(bundle).write_text(hashlib.sha256(bundle.read_bytes()).hexdigest() + "\n", encoding="utf-8")

            reused = assemble_pack(input_dir=input_dir, out_dir=out, evidence_bundle=True)
            assert reused.evidence_bundle_path is not None
            self._assert_pair_valid(reused.evidence_bundle_path)
            with zipfile.ZipFile(reused.evidence_bundle_path, "r") as zf:
                embedded = json.loads(zf.read("receipt.json").decode("utf-8"))
            on_disk = json.loads((reused.pack_dir / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(embedded, on_disk)

    def test_evidence_regenerates_after_public_zip_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path)
            out = tmp_path / "out"
            first = assemble_pack(input_dir=input_dir, out_dir=out, zip_pack=False, evidence_bundle=True)
            assert first.evidence_bundle_path is not None
            first_bytes = first.evidence_bundle_path.read_bytes()

            reused = assemble_pack(input_dir=input_dir, out_dir=out, zip_pack=True, evidence_bundle=True)
            assert reused.evidence_bundle_path is not None
            self.assertNotEqual(reused.evidence_bundle_path.read_bytes(), first_bytes)
            self._assert_pair_valid(reused.evidence_bundle_path)
            with zipfile.ZipFile(reused.evidence_bundle_path, "r") as zf:
                self.assertNotIn("authored_pack.zip", zf.namelist())
                embedded = json.loads(zf.read("receipt.json").decode("utf-8"))
            self.assertEqual(embedded.get("zip_path"), "authored_pack.zip")

    def test_evidence_regenerates_when_current_eligible_file_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path)
            out = tmp_path / "out"
            first = assemble_pack(input_dir=input_dir, out_dir=out, evidence_bundle=True)
            assert first.evidence_bundle_path is not None
            source_record = first.pack_dir / "authored_sources"
            source_record.mkdir()
            (source_record / "late.txt").write_text("late adjunct\n", encoding="utf-8")

            sidecar = self._sidecar(first.evidence_bundle_path)
            valid, errors = pack_module._verify_evidence_pair(first.pack_dir, first.evidence_bundle_path, sidecar)
            self.assertFalse(valid)
            self.assertTrue(any("entry set stale" in error for error in errors), msg=errors)

            reused = assemble_pack(input_dir=input_dir, out_dir=out, evidence_bundle=True)
            assert reused.evidence_bundle_path is not None
            self._assert_pair_valid(reused.evidence_bundle_path)
            with zipfile.ZipFile(reused.evidence_bundle_path, "r") as zf:
                self.assertIn("authored_sources/late.txt", zf.namelist())


if __name__ == "__main__":
    unittest.main()
