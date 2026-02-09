from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__ as EPS_VERSION
from .hkdf import hkdf_sha256
from .manifest import (
    DEFAULT_DERIVATION_VERSION,
    MANIFEST_SCHEMA_VERSION,
    VerificationResult,
    build_manifest,
    collect_artifacts,
    manifest_root_sha256,
    sha256_hex,
)


RECEIPT_SCHEMA_VERSION = "eps.receipt.v1"
PACK_LAYOUT_VERSION = "eps.pack_layout.v1"

DEFAULT_MAX_MANIFEST_BYTES = 4 * 1024 * 1024  # 4 MiB
DEFAULT_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024  # 512 MiB
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _looks_like_windows_drive(path: str) -> bool:
    # "C:foo" and "C:\foo" patterns are ambiguous across platforms.
    return len(path) >= 2 and path[1] == ":" and path[0].isalpha()


def _validate_artifact_relpath(value: object) -> Optional[Path]:
    if not isinstance(value, str):
        return None
    rel = value.strip()
    if not rel:
        return None
    if "\x00" in rel:
        return None
    # Manifest paths are POSIX-style; backslashes tend to be accidental or hostile.
    if "\\" in rel:
        return None
    if rel.startswith("/"):
        return None
    if _looks_like_windows_drive(rel):
        return None
    p = Path(rel)
    if p.is_absolute():
        return None
    if any(part in (".", "..") for part in p.parts):
        return None
    # Current pack layout requires artifacts under payload/.
    if not p.parts or p.parts[0] != "payload":
        return None
    return p


def _sha256_hex_stream(handle, *, max_bytes: Optional[int] = None) -> Tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        n += len(chunk)
        if max_bytes is not None and n > max_bytes:
            raise ValueError(f"stream exceeded max_bytes ({n} > {max_bytes})")
        h.update(chunk)
    return h.hexdigest(), n


def _read_file_bytes_limited(path: Path, *, max_bytes: int) -> bytes:
    try:
        size = int(path.stat().st_size)
        if size > int(max_bytes):
            raise ValueError(f"file too large ({size} > {max_bytes})")
    except OSError:
        pass
    with path.open("rb") as handle:
        data = handle.read(int(max_bytes) + 1)
    if len(data) > int(max_bytes):
        raise ValueError(f"file too large ({len(data)} > {max_bytes})")
    return data


def _read_zip_member_bytes_limited(zf: zipfile.ZipFile, name: str, *, max_bytes: int) -> bytes:
    info = zf.getinfo(name)
    if info.is_dir():
        raise ValueError("zip member is a directory")
    size = int(getattr(info, "file_size", -1))
    if size >= 0 and size > int(max_bytes):
        raise ValueError(f"zip member too large ({size} > {max_bytes})")
    with zf.open(info, "r") as handle:
        data = handle.read(int(max_bytes) + 1)
    if len(data) > int(max_bytes):
        raise ValueError(f"zip member too large ({len(data)} > {max_bytes})")
    return data


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _safe_write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _safe_write_json(path: Path, obj: Dict[str, object]) -> None:
    _ensure_parent(path)
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    path.write_text(payload, encoding="utf-8")


def _copy_payload_files(
    *,
    input_dir: Path,
    pack_dir: Path,
    artifacts: Sequence[Dict[str, object]],
) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for a in artifacts:
        src_rel = str(a.get("source_relpath", "") or "")
        if not src_rel:
            raise ValueError("artifact missing source_relpath")
        src = input_dir / Path(src_rel)
        if not src.is_file():
            raise ValueError(f"artifact source missing: {src}")

        dst_rel = Path("payload") / Path(src_rel)

        dst = pack_dir / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

        out.append(
            {
                "path": dst_rel.as_posix(),
                "sha256": str(a.get("sha256", "")),
                "size_bytes": int(a.get("size_bytes", 0)),
            }
        )
    out.sort(key=lambda d: str(d.get("path", "")))
    return out


def derive_seed_master(
    *,
    root_sha256_hex: str,
    derivation_version: str = DEFAULT_DERIVATION_VERSION,
    entropy_sources_sha256_hex: Optional[str] = None,
) -> bytes:
    """
    Derive the 32-byte seed_master.

    Backwards compatible behavior:
    - If entropy_sources_sha256_hex is None: identical to the v1 derivation (root-only).
    - If provided: mix the sources hash into the HKDF salt, producing a different seed.
    """
    root_bytes = bytes.fromhex(root_sha256_hex)
    info = derivation_version.encode("utf-8")
    salt = b"EPS-SALT-v1"
    if entropy_sources_sha256_hex:
        try:
            src = bytes.fromhex(str(entropy_sources_sha256_hex))
        except Exception:
            src = b""
        # Salt-mixing keeps root as the IKM, and makes the additional sources explicit.
        salt = b"EPS-SALT-v2" + src
    return hkdf_sha256(ikm=root_bytes, length=32, salt=salt, info=info)


def seed_fingerprint_sha256(seed_master: bytes) -> str:
    return sha256_hex(bytes(seed_master))


def _chmod_600(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Best-effort; some filesystems may not support chmod.
        pass


def _pack_dir_for_root(out_dir: Path, root_sha256: str) -> Path:
    return out_dir / root_sha256


@dataclass(frozen=True)
class StampResult:
    pack_dir: Path
    root_sha256: str
    receipt: Dict[str, object]
    seed_master: Optional[bytes] = None


def stamp_pack(
    *,
    input_dir: Path,
    out_dir: Path,
    pack_id: Optional[str] = None,
    notes: Optional[str] = None,
    created_at_utc: Optional[str] = None,
    dice: Optional[Sequence[Tuple[str, int]]] = None,
    include_hidden: bool = False,
    zip_pack: bool = False,
    derive_seed: bool = False,
    entropy_sources_sha256: Optional[str] = None,
    write_seed_files: bool = False,
    print_seed: bool = False,
) -> StampResult:
    input_dir = Path(input_dir).resolve()
    out_dir = Path(out_dir).resolve()
    if not input_dir.is_dir():
        raise ValueError(f"--input must be a directory: {input_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_artifacts = collect_artifacts(input_dir, include_hidden=include_hidden)
    if not raw_artifacts:
        raise ValueError("input directory contains no artifacts")

    # Create a temp pack dir to avoid partially-written packs.
    tmp_dir = out_dir / f".eps_tmp_{os.getpid()}_{int(datetime.now().timestamp())}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        artifact_entries = _copy_payload_files(input_dir=input_dir, pack_dir=tmp_dir, artifacts=raw_artifacts)
        manifest = build_manifest(
            pack_id=pack_id,
            artifacts=artifact_entries,
            notes=notes,
            created_at_utc=created_at_utc,
            dice=dice,
        )
        root_sha = manifest_root_sha256(manifest)
        deriv_version = None
        if derive_seed:
            deriv_version = DEFAULT_DERIVATION_VERSION if not entropy_sources_sha256 else f"{DEFAULT_DERIVATION_VERSION}+SOURCES"

        pack_dir = _pack_dir_for_root(out_dir, root_sha)
        if pack_dir.exists():
            # Idempotent behavior: if the existing pack matches the manifest root, reuse it.
            existing_manifest = pack_dir / "manifest.json"
            if existing_manifest.is_file():
                try:
                    raw = _read_file_bytes_limited(existing_manifest, max_bytes=DEFAULT_MAX_MANIFEST_BYTES)
                    existing = json.loads(raw.decode("utf-8"))
                    if isinstance(existing, dict) and manifest_root_sha256(existing) == root_sha:
                        seed_master: Optional[bytes] = None
                        if derive_seed:
                            seed_master = derive_seed_master(
                                root_sha256_hex=root_sha,
                                entropy_sources_sha256_hex=entropy_sources_sha256,
                            )
                        # Best-effort: ensure requested derived outputs exist when reusing a pack.
                        if zip_pack and not (pack_dir / "entropy_pack.zip").is_file():
                            _write_zip(pack_dir, pack_dir / "entropy_pack.zip")
                        if derive_seed and write_seed_files and seed_master is not None:
                            seed_hex = seed_master.hex()
                            seed_b64 = base64.b64encode(seed_master).decode("ascii")
                            _safe_write_text(pack_dir / "seed_master.hex", seed_hex + "\n")
                            _safe_write_text(pack_dir / "seed_master.b64", seed_b64 + "\n")
                            _chmod_600(pack_dir / "seed_master.hex")
                            _chmod_600(pack_dir / "seed_master.b64")
                        # receipt.json is operational metadata; keep it aligned with the most recent stamp call.
                        receipt = _build_receipt(
                            root_sha256=root_sha,
                            pack_id=pack_id,
                            artifact_entries=artifact_entries,
                            zip_path=Path("entropy_pack.zip") if zip_pack else None,
                            derivation_version=deriv_version,
                            entropy_sources_sha256=entropy_sources_sha256,
                            seed_master=seed_master,
                        )
                        _safe_write_json(pack_dir / "receipt.json", receipt)
                        if print_seed and seed_master is not None:
                            _print_seed_material(seed_master)
                        try:
                            shutil.rmtree(tmp_dir)
                        except Exception:
                            pass
                        return StampResult(pack_dir=pack_dir, root_sha256=root_sha, receipt=receipt, seed_master=seed_master)
                except Exception:
                    pass
            raise FileExistsError(f"pack already exists with different contents: {pack_dir}")

        # Write pack contents into temp dir first.
        _safe_write_json(tmp_dir / "manifest.json", manifest)
        _safe_write_text(tmp_dir / "entropy_root_sha256.txt", root_sha + "\n")

        seed_master: Optional[bytes] = None
        if derive_seed:
            seed_master = derive_seed_master(
                root_sha256_hex=root_sha,
                entropy_sources_sha256_hex=entropy_sources_sha256,
            )
            if write_seed_files:
                seed_hex = seed_master.hex()
                seed_b64 = base64.b64encode(seed_master).decode("ascii")
                _safe_write_text(tmp_dir / "seed_master.hex", seed_hex + "\n")
                _safe_write_text(tmp_dir / "seed_master.b64", seed_b64 + "\n")
                _chmod_600(tmp_dir / "seed_master.hex")
                _chmod_600(tmp_dir / "seed_master.b64")

        if zip_pack:
            _write_zip(tmp_dir, tmp_dir / "entropy_pack.zip")

        receipt = _build_receipt(
            root_sha256=root_sha,
            pack_id=pack_id,
            artifact_entries=artifact_entries,
            zip_path=Path("entropy_pack.zip") if zip_pack else None,
            derivation_version=deriv_version,
            entropy_sources_sha256=entropy_sources_sha256,
            seed_master=seed_master,
        )
        _safe_write_json(tmp_dir / "receipt.json", receipt)

        # Atomic-ish move: rename tmp dir into content-addressed target.
        tmp_dir.replace(pack_dir)

        if print_seed and seed_master is not None:
            _print_seed_material(seed_master)

        return StampResult(pack_dir=pack_dir, root_sha256=root_sha, receipt=receipt, seed_master=seed_master)
    except Exception:
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass
        raise


def _build_receipt(
    *,
    root_sha256: str,
    pack_id: Optional[str],
    artifact_entries: Sequence[Dict[str, object]],
    zip_path: Optional[Path],
    derivation_version: Optional[str],
    entropy_sources_sha256: Optional[str],
    seed_master: Optional[bytes],
) -> Dict[str, object]:
    total_bytes = sum(int(a.get("size_bytes", 0) or 0) for a in artifact_entries)
    receipt: Dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "tool": "eps",
        "tool_version": str(EPS_VERSION),
        "pack_layout": PACK_LAYOUT_VERSION,
        "entropy_schema_version": MANIFEST_SCHEMA_VERSION,
        "entropy_root_sha256": root_sha256,
        "artifact_count": int(len(artifact_entries)),
        "artifact_bytes": int(total_bytes),
        "stamped_at_utc": _utc_now_iso(),
    }
    if pack_id:
        receipt["pack_id"] = str(pack_id)
    if zip_path is not None:
        # Avoid embedding absolute local paths in receipts.
        receipt["zip_path"] = str(Path(str(zip_path)).name)
    if derivation_version:
        receipt["derivation_version"] = derivation_version
    if entropy_sources_sha256:
        receipt["entropy_sources_sha256"] = str(entropy_sources_sha256)
    if seed_master is not None:
        receipt["seed_fingerprint_sha256"] = seed_fingerprint_sha256(seed_master)
    return receipt


def _print_seed_material(seed_master: bytes) -> None:
    seed_hex = seed_master.hex()
    seed_b64 = base64.b64encode(seed_master).decode("ascii")
    print("seed_master.hex:", seed_hex)
    print("seed_master.b64:", seed_b64)


def _write_zip(pack_dir: Path, zip_path: Path) -> None:
    # Avoid including seed files by default. They are sensitive and should be injected per-run.
    exclude = {"seed_master.hex", "seed_master.b64", zip_path.name}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(pack_dir.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(pack_dir).as_posix()
            if rel.startswith("entropy_sources/") or rel.startswith("entropy_sources\\"):
                continue
            if rel in exclude:
                continue
            zf.write(path, arcname=rel)


def verify_pack(
    pack_path: Path,
    *,
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    reject_duplicate_zip_members: bool = True,
) -> VerificationResult:
    pack_path = Path(pack_path).resolve()
    errors: List[str] = []
    file_count = 0
    total_bytes = 0
    try:
        max_manifest_bytes = int(max_manifest_bytes)
        max_artifact_bytes = int(max_artifact_bytes)
        max_total_bytes = int(max_total_bytes)
    except Exception:
        return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["invalid verify limits"])
    if max_manifest_bytes <= 0 or max_artifact_bytes <= 0 or max_total_bytes <= 0:
        return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["invalid verify limits"])

    if pack_path.is_dir():
        manifest_path = pack_path / "manifest.json"
        if not manifest_path.is_file():
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["missing manifest.json"])
        try:
            raw = _read_file_bytes_limited(manifest_path, max_bytes=max_manifest_bytes)
            manifest = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"invalid manifest.json: {exc}"])
        if not isinstance(manifest, dict):
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["manifest.json must be an object"])
        root_sha = manifest_root_sha256(manifest)

        expected_path = pack_path / "entropy_root_sha256.txt"
        if expected_path.is_file():
            try:
                raw_expected = _read_file_bytes_limited(expected_path, max_bytes=256)
                expected = raw_expected.decode("utf-8").strip()
            except Exception:
                expected = ""
            if expected and expected != root_sha:
                errors.append("entropy_root_sha256.txt does not match manifest root")

        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            errors.append("manifest.artifacts missing or empty")
            return VerificationResult(ok=False, root_sha256=root_sha, file_count=0, total_bytes=0, errors=errors)

        for i, a in enumerate(artifacts):
            if not isinstance(a, dict):
                errors.append(f"artifact[{i}] not an object")
                continue
            rel_path = _validate_artifact_relpath(a.get("path"))
            sha = a.get("sha256")
            size = a.get("size_bytes")
            if rel_path is None:
                errors.append(f"artifact[{i}].path invalid")
                continue
            if not isinstance(sha, str) or len(sha) != 64:
                errors.append(f"artifact[{i}].sha256 invalid")
                continue
            if not isinstance(size, int) or size < 0:
                errors.append(f"artifact[{i}].size_bytes invalid")
                continue
            if size > max_artifact_bytes:
                errors.append(f"artifact[{i}] too large: {rel_path.as_posix()} size_bytes={size} cap={max_artifact_bytes}")
                continue
            if total_bytes + int(size) > max_total_bytes:
                errors.append(f"pack too large (cap exceeded): cap={max_total_bytes}")
                continue
            target = pack_path / rel_path
            # Guard against path traversal and symlink escapes.
            try:
                resolved = target.resolve()
            except Exception:
                resolved = target
            if not resolved.is_relative_to(pack_path):
                errors.append(f"artifact[{i}] path escapes pack dir: {rel_path.as_posix()}")
                continue
            if target.is_symlink():
                errors.append(f"artifact[{i}] is a symlink (refusing): {rel_path.as_posix()}")
                continue
            if not target.is_file():
                errors.append(f"missing artifact file: {rel_path.as_posix()}")
                continue
            actual_size = target.stat().st_size
            if actual_size != size:
                errors.append(f"size mismatch: {rel_path.as_posix()} expected={size} actual={actual_size}")
                continue
            try:
                with target.open("rb") as handle:
                    actual_sha, n = _sha256_hex_stream(handle, max_bytes=size)
            except Exception as exc:
                errors.append(f"failed to read artifact: {rel_path.as_posix()}: {exc}")
                continue
            if n != size:
                errors.append(f"size mismatch: {rel_path.as_posix()} expected={size} actual={n}")
                continue
            if actual_sha != sha:
                errors.append(f"sha256 mismatch: {rel_path.as_posix()}")
                continue
            file_count += 1
            total_bytes += int(size)

        return VerificationResult(ok=not errors, root_sha256=root_sha, file_count=file_count, total_bytes=total_bytes, errors=errors)

    if pack_path.is_file() and pack_path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(pack_path, "r") as zf:
                if reject_duplicate_zip_members:
                    names = [zi.filename for zi in zf.infolist()]
                    if len(names) != len(set(names)):
                        return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["zip contains duplicate member names"])
                try:
                    raw = _read_zip_member_bytes_limited(zf, "manifest.json", max_bytes=max_manifest_bytes)
                except KeyError:
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["missing manifest.json in zip"])
                try:
                    manifest = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"invalid manifest.json in zip: {exc}"])
                if not isinstance(manifest, dict):
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["manifest.json must be an object"])

                root_sha = manifest_root_sha256(manifest)
                try:
                    raw_expected = _read_zip_member_bytes_limited(zf, "entropy_root_sha256.txt", max_bytes=256)
                    expected = raw_expected.decode("utf-8").strip()
                    if expected and expected != root_sha:
                        errors.append("entropy_root_sha256.txt does not match manifest root")
                except KeyError:
                    # Optional
                    pass

                artifacts = manifest.get("artifacts")
                if not isinstance(artifacts, list) or not artifacts:
                    errors.append("manifest.artifacts missing or empty")
                    return VerificationResult(ok=False, root_sha256=root_sha, file_count=0, total_bytes=0, errors=errors)

                for i, a in enumerate(artifacts):
                    if not isinstance(a, dict):
                        errors.append(f"artifact[{i}] not an object")
                        continue
                    rel_path = _validate_artifact_relpath(a.get("path"))
                    sha = a.get("sha256")
                    size = a.get("size_bytes")
                    if rel_path is None:
                        errors.append(f"artifact[{i}].path invalid")
                        continue
                    rel_s = rel_path.as_posix()
                    if not isinstance(sha, str) or len(sha) != 64:
                        errors.append(f"artifact[{i}].sha256 invalid")
                        continue
                    if not isinstance(size, int) or size < 0:
                        errors.append(f"artifact[{i}].size_bytes invalid")
                        continue
                    if size > max_artifact_bytes:
                        errors.append(f"artifact[{i}] too large: {rel_s} size_bytes={size} cap={max_artifact_bytes}")
                        continue
                    if total_bytes + int(size) > max_total_bytes:
                        errors.append(f"pack too large (cap exceeded): cap={max_total_bytes}")
                        continue
                    try:
                        info = zf.getinfo(rel_s)
                    except KeyError:
                        errors.append(f"missing artifact file in zip: {rel_s}")
                        continue
                    if info.is_dir():
                        errors.append(f"artifact[{i}] is a directory in zip: {rel_s}")
                        continue
                    zip_size = int(getattr(info, "file_size", -1))
                    if zip_size != size:
                        errors.append(f"size mismatch: {rel_s} expected={size} actual={zip_size}")
                        continue
                    try:
                        with zf.open(info, "r") as handle:
                            actual_sha, n = _sha256_hex_stream(handle, max_bytes=size)
                    except Exception as exc:
                        errors.append(f"failed to read artifact in zip: {rel_s}: {exc}")
                        continue
                    if n != size:
                        errors.append(f"size mismatch: {rel_s} expected={size} actual={n}")
                        continue
                    if actual_sha != sha:
                        errors.append(f"sha256 mismatch: {rel_s}")
                        continue
                    file_count += 1
                    total_bytes += int(size)

                return VerificationResult(ok=not errors, root_sha256=root_sha, file_count=file_count, total_bytes=total_bytes, errors=errors)
        except zipfile.BadZipFile as exc:
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"invalid zip: {exc}"])

    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"unsupported pack path: {pack_path}"])
