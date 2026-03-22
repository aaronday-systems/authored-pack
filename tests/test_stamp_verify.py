from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from eps.manifest import manifest_root_sha256, stable_dumps
from eps.pack import stamp_pack, verify_pack


class TestStampVerify(unittest.TestCase):
    def test_stamp_and_verify_dir_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")
            (input_dir / "sub").mkdir()
            (input_dir / "sub" / "b.bin").write_bytes(b"\x00\x01\x02")

            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                pack_id="test_pack",
                zip_pack=True,
                derive_seed=True,
                entropy_sources_sha256=None,
                evidence_bundle=True,
                write_seed_files=False,
                print_seed=False,
            )
            self.assertTrue(res.pack_dir.is_dir())
            self.assertEqual(len(res.root_sha256), 64)

            vr = verify_pack(res.pack_dir)
            self.assertTrue(vr.ok, msg=f"errors: {vr.errors}")
            self.assertEqual(vr.root_sha256, res.root_sha256)
            self.assertEqual(vr.file_count, 2)

            zip_path = res.pack_dir / "entropy_pack.zip"
            self.assertTrue(zip_path.is_file())
            vz = verify_pack(zip_path)
            self.assertTrue(vz.ok, msg=f"errors: {vz.errors}")
            self.assertEqual(vz.root_sha256, res.root_sha256)
            self.assertEqual(vz.file_count, 2)

            ev_zip = res.pack_dir / f"eps_evidence_{res.root_sha256}.zip"
            self.assertTrue(ev_zip.is_file())
            # Receipt should expose evidence bundle identity fields for downstream agents.
            receipt = (res.pack_dir / "receipt.json").read_text(encoding="utf-8")
            self.assertIn("evidence_bundle_path", receipt)
            self.assertIn("evidence_bundle_sha256", receipt)
            with zipfile.ZipFile(ev_zip, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("manifest.json", names)
                self.assertIn("receipt.json", names)
                self.assertIn("entropy_root_sha256.txt", names)
                self.assertIn("evidence_manifest.json", names)
                self.assertIn("evidence_manifest_sha256.txt", names)

            # Idempotent re-stamp: same input should not fail.
            res2 = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                pack_id="test_pack",
                zip_pack=False,
                derive_seed=True,
            )
            self.assertEqual(res2.root_sha256, res.root_sha256)
            self.assertEqual(res2.pack_dir, res.pack_dir)
            manifest = json.loads((res.pack_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "entropy.pack.v2")
            self.assertEqual(manifest["derivation"]["mode"], "root-only")
            receipt_obj = json.loads((res.pack_dir / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt_obj["schema_version"], "eps.receipt.v2")
            self.assertEqual(receipt_obj["entropy_schema_version"], "entropy.pack.v2")

    def test_manifest_root_changes_when_derivation_metadata_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            root_only = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=False,
            )
            derived = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=True,
            )
            self.assertNotEqual(root_only.root_sha256, derived.root_sha256)

    def test_stamp_exclude_relpaths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "keep.txt").write_text("keep", encoding="utf-8")
            (input_dir / "drop.txt").write_text("drop", encoding="utf-8")

            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=False,
                exclude_relpaths=["drop.txt"],
            )
            vr = verify_pack(res.pack_dir)
            self.assertTrue(vr.ok, msg=f"errors: {vr.errors}")
            self.assertEqual(vr.file_count, 1)
            manifest = (res.pack_dir / "manifest.json").read_text(encoding="utf-8")
            self.assertIn("keep.txt", manifest)
            self.assertNotIn("drop.txt", manifest)

    def test_seed_changes_when_sources_hash_is_mixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res1 = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=True,
                entropy_sources_sha256=None,
                evidence_bundle=False,
                write_seed_files=False,
                print_seed=False,
            )
            self.assertIsNotNone(res1.seed_master)

            res2 = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=True,
                entropy_sources_sha256=hashlib.sha256(b"demo").hexdigest(),
                evidence_bundle=False,
                write_seed_files=False,
                print_seed=False,
            )
            self.assertIsNotNone(res2.seed_master)
            self.assertNotEqual(res1.seed_master, res2.seed_master)

    def test_verify_accepts_legacy_v1_dir_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "v1_pack"
            payload_dir = pack_dir / "payload"
            payload_dir.mkdir(parents=True)
            data = b"hello"
            (payload_dir / "a.txt").write_bytes(data)
            manifest = {
                "schema_version": "entropy.pack.v1",
                "artifacts": [
                    {
                        "path": "payload/a.txt",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size_bytes": len(data),
                    }
                ],
            }
            root = manifest_root_sha256(manifest)
            (pack_dir / "manifest.json").write_text(stable_dumps(manifest), encoding="utf-8")
            (pack_dir / "entropy_root_sha256.txt").write_text(root + "\n", encoding="utf-8")

            dir_result = verify_pack(pack_dir)
            self.assertTrue(dir_result.ok, msg=f"errors: {dir_result.errors}")

            zip_path = tmp_path / "v1_pack.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", stable_dumps(manifest))
                zf.writestr("entropy_root_sha256.txt", root + "\n")
                zf.writestr("payload/a.txt", data)
            zip_result = verify_pack(zip_path)
            self.assertTrue(zip_result.ok, msg=f"errors: {zip_result.errors}")

    def test_verify_rejects_v2_receipt_mismatch_in_dir_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=True,
                derive_seed=True,
            )

            receipt_path = res.pack_dir / "receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["entropy_root_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")

            dir_result = verify_pack(res.pack_dir)
            self.assertFalse(dir_result.ok, msg=f"errors: {dir_result.errors}")
            self.assertTrue(any("receipt.json entropy_root_sha256 mismatch" in e for e in dir_result.errors))

            zip_path = tmp_path / "bad_receipt.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", (res.pack_dir / "manifest.json").read_text(encoding="utf-8"))
                zf.writestr("entropy_root_sha256.txt", (res.pack_dir / "entropy_root_sha256.txt").read_text(encoding="utf-8"))
                zf.writestr("receipt.json", receipt_path.read_text(encoding="utf-8"))
                zf.writestr("payload/a.txt", (res.pack_dir / "payload" / "a.txt").read_bytes())

            zip_result = verify_pack(zip_path)
            self.assertFalse(zip_result.ok, msg=f"errors: {zip_result.errors}")
            self.assertTrue(any("receipt.json entropy_root_sha256 mismatch" in e for e in zip_result.errors))

    def test_v2_public_zip_contains_final_receipt_and_excludes_private_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=True,
                derive_seed=True,
                evidence_bundle=True,
                write_seed_files=True,
            )

            zip_path = res.pack_dir / "entropy_pack.zip"
            on_disk_receipt = json.loads((res.pack_dir / "receipt.json").read_text(encoding="utf-8"))
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("manifest.json", names)
                self.assertIn("entropy_root_sha256.txt", names)
                self.assertIn("receipt.json", names)
                self.assertIn("payload/a.txt", names)
                self.assertNotIn("seed_master.hex", names)
                self.assertNotIn("seed_master.b64", names)
                self.assertFalse(any(name.startswith("eps_evidence_") for name in names))
                self.assertFalse(any(name.endswith(".sha256") for name in names))
                zipped_receipt = json.loads(zf.read("receipt.json").decode("utf-8"))
                self.assertEqual(zipped_receipt, on_disk_receipt)

    def test_verify_rejects_path_traversal_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Path traversal attempt: artifact path escapes pack dir.
            pack_dir = tmp_path / "pack_bad_path"
            pack_dir.mkdir()
            (pack_dir / "manifest.json").write_text(
                '{"schema_version":"entropy.pack.v1","artifacts":[{"path":"../outside.txt","sha256":"%s","size_bytes":1}]}'
                % ("0" * 64),
                encoding="utf-8",
            )
            res = verify_pack(pack_dir)
            self.assertFalse(res.ok)
            self.assertTrue(any("path" in e and "invalid" in e for e in res.errors), msg=f"errors: {res.errors}")

            # Symlink attempt: artifact is a symlink (even if it points inside the pack).
            pack_dir2 = tmp_path / "pack_bad_symlink"
            payload = pack_dir2 / "payload"
            payload.mkdir(parents=True)
            (pack_dir2 / "manifest.json").write_text(
                '{"schema_version":"entropy.pack.v1","artifacts":[{"path":"payload/link.txt","sha256":"%s","size_bytes":5}]}'
                % hashlib.sha256(b"hello").hexdigest(),
                encoding="utf-8",
            )
            (payload / "real.txt").write_text("hello", encoding="utf-8")
            (payload / "link.txt").symlink_to(payload / "real.txt")
            res2 = verify_pack(pack_dir2)
            self.assertFalse(res2.ok)
            self.assertTrue(any("symlink" in e for e in res2.errors), msg=f"errors: {res2.errors}")

            # Zip: invalid artifact path should be rejected without reading arbitrary entries.
            zip_path = tmp_path / "bad.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "manifest.json",
                    '{"schema_version":"entropy.pack.v1","artifacts":[{"path":"../outside.txt","sha256":"%s","size_bytes":1}]}'
                    % ("0" * 64),
                )
            rz = verify_pack(zip_path)
            self.assertFalse(rz.ok)
            self.assertTrue(any("path" in e and "invalid" in e for e in rz.errors), msg=f"errors: {rz.errors}")

    def test_verify_rejects_duplicate_zip_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "dup.zip"

            # Write the same member name twice.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr("manifest.json", '{"schema_version":"entropy.pack.v1","artifacts":[]}')
                    zf.writestr("manifest.json", '{"schema_version":"entropy.pack.v1","artifacts":[]}')

            res = verify_pack(zip_path)
            self.assertFalse(res.ok)
            self.assertTrue(any("duplicate" in e for e in res.errors), msg=f"errors: {res.errors}")

    def test_verify_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "pack"
            payload = pack_dir / "payload"
            payload.mkdir(parents=True)
            (pack_dir / "manifest.json").write_text(
                '{"schema_version":"entropy.pack.v1","artifacts":[{"path":"payload/a.txt","sha256":"%s","size_bytes":5}]}'
                % hashlib.sha256(b"hello").hexdigest(),
                encoding="utf-8",
            )
            (payload / "a.txt").write_text("hello", encoding="utf-8")

            # Per-artifact cap.
            r1 = verify_pack(pack_dir, max_artifact_bytes=1)
            self.assertFalse(r1.ok)
            self.assertTrue(any("too large" in e for e in r1.errors), msg=f"errors: {r1.errors}")

            # Manifest cap.
            r2 = verify_pack(pack_dir, max_manifest_bytes=1)
            self.assertFalse(r2.ok)
            self.assertTrue(any("invalid manifest.json" in e for e in r2.errors), msg=f"errors: {r2.errors}")

    def test_verify_rejects_extra_payload_file_in_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=False,
                derive_seed=False,
            )
            (res.pack_dir / "payload" / "evil.sh").write_text("echo pwn\n", encoding="utf-8")

            vr = verify_pack(res.pack_dir)
            self.assertFalse(vr.ok, msg=f"errors: {vr.errors}")
            self.assertTrue(any("unexpected payload files present" in e for e in vr.errors), msg=f"errors: {vr.errors}")

    def test_verify_rejects_extra_payload_file_in_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=True,
                derive_seed=False,
            )
            zip_path = res.pack_dir / "entropy_pack.zip"
            with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("payload/evil.sh", "echo pwn\n")

            vr = verify_pack(zip_path)
            self.assertFalse(vr.ok, msg=f"errors: {vr.errors}")
            self.assertTrue(any("unexpected payload files present" in e for e in vr.errors), msg=f"errors: {vr.errors}")

    def test_verify_dir_and_zip_emit_identical_errors_for_same_malformed_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "malformed_pack"
            payload_dir = pack_dir / "payload"
            payload_dir.mkdir(parents=True)

            bad_manifest = (
                '{"schema_version":"entropy.pack.v1","artifacts":['
                '{"path":"../outside.txt","sha256":"%s","size_bytes":1},'
                '{"path":"payload/a.txt","sha256":"bad","size_bytes":1}'
                "]}"
            ) % ("0" * 64)
            (pack_dir / "manifest.json").write_text(bad_manifest, encoding="utf-8")
            (payload_dir / "extra.txt").write_text("extra", encoding="utf-8")

            zip_path = tmp_path / "malformed_pack.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", bad_manifest)
                zf.writestr("payload/extra.txt", "extra")

            dir_result = verify_pack(pack_dir)
            zip_result = verify_pack(zip_path)

            self.assertFalse(dir_result.ok, msg=f"errors: {dir_result.errors}")
            self.assertFalse(zip_result.ok, msg=f"errors: {zip_result.errors}")
            self.assertEqual(zip_result.errors, dir_result.errors)

    def test_verify_v2_requires_matching_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "input"
            out_dir = tmp_path / "out"
            input_dir.mkdir()
            (input_dir / "a.txt").write_text("hello", encoding="utf-8")

            res = stamp_pack(
                input_dir=input_dir,
                out_dir=out_dir,
                zip_pack=True,
                derive_seed=True,
            )
            receipt_path = res.pack_dir / "receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["entropy_root_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            dir_result = verify_pack(res.pack_dir)
            self.assertFalse(dir_result.ok)
            self.assertTrue(any("receipt.json entropy_root_sha256 mismatch" in e for e in dir_result.errors))

            zip_path = res.pack_dir / "entropy_pack.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for src in sorted(res.pack_dir.rglob("*")):
                    if src.is_dir():
                        continue
                    rel = src.relative_to(res.pack_dir).as_posix()
                    if rel.endswith(".sha256") or rel.startswith("eps_evidence_") or rel.startswith("entropy_sources/"):
                        continue
                    zf.write(src, arcname=rel)
            zip_result = verify_pack(zip_path)
            self.assertFalse(zip_result.ok)
            self.assertTrue(any("receipt.json entropy_root_sha256 mismatch" in e for e in zip_result.errors))


if __name__ == "__main__":
    unittest.main()
