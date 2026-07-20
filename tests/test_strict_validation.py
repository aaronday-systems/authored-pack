from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from authored_pack.pack import assemble_pack, inspect_pack, verify_pack


class TestStrictValidation(unittest.TestCase):
    def _assemble_pack(self, tmp_path: Path, *, derive_seed: bool = False):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.txt").write_text("hello", encoding="utf-8")
        return assemble_pack(
            input_dir=input_dir,
            out_dir=tmp_path / "out",
            pack_id="fixture",
            created_at_utc="2026-07-20T12:00:00Z",
            zip_pack=True,
            derive_seed=derive_seed,
        )

    def _rewrite_zip(self, pack_dir: Path, zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in ("manifest.json", "pack_root_sha256.txt", "receipt.json", "payload/a.txt"):
                zf.writestr(name, (pack_dir / name).read_bytes())

    def _assert_dir_and_zip_reject(self, pack_dir: Path, tmp_path: Path, expected: str) -> None:
        zip_path = tmp_path / "malformed.zip"
        self._rewrite_zip(pack_dir, zip_path)
        for label, candidate in (("directory", pack_dir), ("zip", zip_path)):
            with self.subTest(storage=label):
                result = verify_pack(candidate)
                self.assertFalse(result.ok, msg=f"unexpected pass for {label}")
                self.assertTrue(any(expected in error for error in result.errors), msg=result.errors)
                summary = inspect_pack(candidate)
                self.assertFalse(summary["verification_ok"])
                self.assertTrue(any(expected in error for error in summary["verification_errors"]), msg=summary)

    def test_malformed_artifact_structures_are_rejected_before_payload_work(self) -> None:
        cases = {
            "scalar artifact": ([1], "artifact[0] not an object"),
            "boolean size": (
                [{"path": "payload/a.txt", "sha256": "0" * 64, "size_bytes": True}],
                "artifact[0].size_bytes invalid",
            ),
            "missing sha": (
                [{"path": "payload/a.txt", "size_bytes": 5}],
                "artifact[0].sha256 missing",
            ),
            "missing path": (
                [{"sha256": "0" * 64, "size_bytes": 5}],
                "artifact[0].path missing",
            ),
        }
        for name, (artifacts, expected) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                res = self._assemble_pack(tmp_path)
                manifest_path = res.pack_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["artifacts"] = artifacts
                manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
                self._assert_dir_and_zip_reject(res.pack_dir, tmp_path, expected)

    def test_noncanonical_manifest_paths_are_rejected_exactly(self) -> None:
        cases = {
            "leading space": " payload/a.txt",
            "trailing space": "payload/a.txt ",
            "empty segment": "payload//a.txt",
            "dot segment": "payload/./a.txt",
            "parent segment": "payload/x/../a.txt",
            "backslash": "payload\\a.txt",
            "absolute": "/payload/a.txt",
            "drive": "C:/payload/a.txt",
            "nul": "payload/a\x00.txt",
            "c0": "payload/a\x1b.txt",
            "del": "payload/a\x7f.txt",
            "c1": "payload/a\x9b.txt",
        }
        for name, path in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                res = self._assemble_pack(tmp_path)
                manifest_path = res.pack_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["artifacts"][0]["path"] = path
                manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
                self._assert_dir_and_zip_reject(res.pack_dir, tmp_path, "artifact[0].path invalid")

    def test_duplicate_json_keys_and_non_finite_values_are_rejected(self) -> None:
        cases = {
            "duplicate key": (
                '{"schema_version":"authored.pack.v1","schema_version":"authored.pack.v1","artifacts":[]}',
                "duplicate JSON object key: schema_version",
            ),
            "non-finite": (
                '{"schema_version":"authored.pack.v1","artifacts":[],"payload_root_sha256":NaN}',
                "non-finite JSON value not allowed: NaN",
            ),
        }
        for name, (raw, expected) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                res = self._assemble_pack(tmp_path)
                (res.pack_dir / "manifest.json").write_text(raw, encoding="utf-8")
                self._assert_dir_and_zip_reject(res.pack_dir, tmp_path, expected)

    def test_contradictory_or_incomplete_receipts_fail_in_directory_and_zip(self) -> None:
        cases = {
            "missing tool": (lambda r: r.pop("tool"), "receipt.json missing tool"),
            "wrong tool": (lambda r: r.__setitem__("tool", "other"), "receipt.json tool invalid"),
            "missing version": (lambda r: r.pop("tool_version"), "receipt.json missing tool_version"),
            "wrong layout": (lambda r: r.__setitem__("pack_layout", "other"), "receipt.json pack_layout mismatch"),
            "wrong pack id": (lambda r: r.__setitem__("pack_id", "other"), "receipt.json pack_id mismatch"),
            "boolean count": (lambda r: r.__setitem__("artifact_count", True), "receipt.json artifact_count invalid"),
            "wrong byte total": (lambda r: r.__setitem__("artifact_bytes", 6), "receipt.json artifact_bytes mismatch"),
            "bad timestamp": (lambda r: r.__setitem__("stamped_at_utc", "yesterday"), "receipt.json stamped_at_utc invalid"),
            "bad zip path": (lambda r: r.__setitem__("zip_path", "../pack.zip"), "receipt.json zip_path invalid"),
        }
        for name, (mutate, expected) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                res = self._assemble_pack(tmp_path)
                receipt_path = res.pack_dir / "receipt.json"
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                mutate(receipt)
                receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
                self._assert_dir_and_zip_reject(res.pack_dir, tmp_path, expected)

    def test_derived_seed_fingerprint_is_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            res = self._assemble_pack(tmp_path, derive_seed=True)
            receipt_path = res.pack_dir / "receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["derived_seed_fingerprint_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
            self._assert_dir_and_zip_reject(
                res.pack_dir,
                tmp_path,
                "receipt.json derived_seed_fingerprint_sha256 mismatch",
            )


if __name__ == "__main__":
    unittest.main()
