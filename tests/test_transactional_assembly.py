from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import authored_pack.pack as pack_module
from authored_pack.pack import assemble_pack, verify_pack


class TestTransactionalAssembly(unittest.TestCase):
    def _input(self, root: Path, name: str, content: bytes) -> Path:
        input_dir = root / name
        input_dir.mkdir()
        (input_dir / "a.txt").write_bytes(content)
        return input_dir

    def test_payload_sidecars_and_nested_zips_are_not_filtered_from_projections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            (input_dir / "nested").mkdir(parents=True)
            (input_dir / "checksum.sha256").write_text("payload sidecar\n", encoding="utf-8")
            (input_dir / "nested" / "archive.zip").write_bytes(b"payload zip bytes")

            res = assemble_pack(
                input_dir=input_dir,
                out_dir=tmp_path / "out",
                zip_pack=True,
                evidence_bundle=True,
            )

            assert res.zip_path is not None
            with zipfile.ZipFile(res.zip_path, "r") as zf:
                self.assertIn("payload/checksum.sha256", zf.namelist())
                self.assertIn("payload/nested/archive.zip", zf.namelist())
            assert res.evidence_bundle_path is not None
            with zipfile.ZipFile(res.evidence_bundle_path, "r") as zf:
                self.assertIn("payload/checksum.sha256", zf.namelist())
                self.assertIn("payload/nested/archive.zip", zf.namelist())

    def test_reuse_rejects_internally_valid_zip_for_a_different_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_a = self._input(tmp_path, "input_a", b"alpha")
            input_b = self._input(tmp_path, "input_b", b"beta")
            out = tmp_path / "out"
            alpha = assemble_pack(input_dir=input_a, out_dir=out, zip_pack=True)
            beta = assemble_pack(input_dir=input_b, out_dir=out, zip_pack=True)
            assert alpha.zip_path is not None and beta.zip_path is not None
            alpha.zip_path.write_bytes(beta.zip_path.read_bytes())

            with self.assertRaisesRegex(ValueError, "authored_pack.zip.*root|root.*authored_pack.zip"):
                assemble_pack(input_dir=input_a, out_dir=out, zip_pack=True)

    def test_invalid_generated_zip_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path, "input", b"alpha")
            out = tmp_path / "out"

            def write_invalid_zip(_pack_dir: Path, zip_path: Path, **_kwargs) -> None:
                zip_path.write_bytes(b"not a zip")

            with patch.object(pack_module, "_write_zip", side_effect=write_invalid_zip):
                with self.assertRaisesRegex(ValueError, "generated authored_pack.zip failed verification"):
                    assemble_pack(input_dir=input_dir, out_dir=out, zip_pack=True)

            self.assertEqual(list(out.iterdir()), [])

    def test_projection_receipt_mismatch_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path, "input", b"alpha")
            out = tmp_path / "out"
            real_write_zip = pack_module._write_zip

            def write_mismatched_zip(pack_dir: Path, zip_path: Path, **kwargs) -> None:
                real_write_zip(pack_dir, zip_path, **kwargs)
                with zipfile.ZipFile(zip_path, "r") as source:
                    members = {name: source.read(name) for name in source.namelist()}
                receipt = json.loads(members["receipt.json"].decode("utf-8"))
                receipt["operator_note"] = "zip only"
                members["receipt.json"] = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
                with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
                    for name, payload in members.items():
                        target.writestr(name, payload)

            with patch.object(pack_module, "_write_zip", side_effect=write_mismatched_zip):
                with self.assertRaisesRegex(ValueError, "receipt mismatch"):
                    assemble_pack(input_dir=input_dir, out_dir=out, zip_pack=True)
            self.assertEqual(list(out.iterdir()), [])

    def test_source_record_has_identical_new_and_reuse_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path, "input", b"alpha")
            out = tmp_path / "out"
            first = assemble_pack(input_dir=input_dir, out_dir=out)
            record = tmp_path / "record"
            record.mkdir()
            (record / "sources.index.json").write_text("[]\n", encoding="utf-8")
            fields = {
                "authored_sources_audit_status": "ok",
                "authored_sources_audit_requested_count": 1,
                "authored_sources_audit_materialized_count": 1,
                "authored_sources_audit_warnings": [],
            }

            reused = assemble_pack(
                input_dir=input_dir,
                out_dir=out,
                zip_pack=True,
                evidence_bundle=True,
                source_record_dir=record,
                source_record_receipt_fields=fields,
            )
            self.assertEqual(reused.root_sha256, first.root_sha256)
            self.assertEqual((reused.pack_dir / "authored_sources" / "sources.index.json").read_text(), "[]\n")
            for key, value in fields.items():
                self.assertEqual(reused.receipt[key], value)
            assert reused.zip_path is not None
            with zipfile.ZipFile(reused.zip_path, "r") as zf:
                self.assertNotIn("authored_sources/sources.index.json", zf.namelist())
                self.assertEqual(json.loads(zf.read("receipt.json"))["authored_sources_audit_status"], "ok")
            assert reused.evidence_bundle_path is not None
            with zipfile.ZipFile(reused.evidence_bundle_path, "r") as zf:
                self.assertIn("authored_sources/sources.index.json", zf.namelist())

    def test_arbitrary_before_finalize_callback_is_not_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = self._input(tmp_path, "input", b"alpha")

            def mutate_payload(candidate: Path):
                (candidate / "payload" / "a.txt").write_bytes(b"mutated")

            with self.assertRaisesRegex(TypeError, "before_finalize"):
                assemble_pack(  # type: ignore[call-arg]
                    input_dir=input_dir,
                    out_dir=tmp_path / "out",
                    before_finalize=mutate_payload,
                )

    @unittest.skipUnless(os.pathconf(tempfile.gettempdir(), "PC_NAME_MAX") >= 255, "filesystem name limit below 255")
    def test_legal_maximum_length_filename_assembles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            input_dir.mkdir()
            filename = "a" * 251 + ".txt"
            (input_dir / filename).write_bytes(b"max")
            res = assemble_pack(input_dir=input_dir, out_dir=tmp_path / "out", zip_pack=True)
            self.assertTrue(verify_pack(res.pack_dir).ok)
            assert res.zip_path is not None
            self.assertTrue(verify_pack(res.zip_path).ok)


if __name__ == "__main__":
    unittest.main()
