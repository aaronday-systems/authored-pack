from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import __version__ as AUTHORED_PACK_VERSION
from .hkdf import hkdf_sha256
from .manifest import (
    DEFAULT_DERIVATION_VERSION,
    MANIFEST_SCHEMA_VERSION,
    VerificationResult,
    build_manifest,
    collect_artifacts,
    manifest_root_sha256,
    payload_root_sha256,
    sha256_hex,
)
from .safeio import read_trusted_bytes_limited, trusted_copy_with_sha256
from .safeio import trusted_binary_reader, trusted_sha256_hex


RECEIPT_SCHEMA_VERSION = "authored.receipt.v1"
PACK_LAYOUT_VERSION = "authored.pack_layout.v1"
LEGACY_MANIFEST_SCHEMA_VERSION = "entropy.pack.v1"
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = {
    LEGACY_MANIFEST_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
}

DEFAULT_MAX_MANIFEST_BYTES = 4 * 1024 * 1024  # 4 MiB
DEFAULT_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024  # 512 MiB
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
DEFAULT_MAX_ZIP_MEMBERS = 10_000
EVIDENCE_SCHEMA_VERSION = "authored.evidence.v1"
PACK_ROOT_ALIAS_FILENAME = "pack_root_sha256.txt"
LEGACY_ROOT_ALIAS_FILENAME = "entropy_root_sha256.txt"
RESERVED_RECEIPT_KEYS: Set[str] = {
    "schema_version",
    "tool",
    "tool_version",
    "pack_layout",
    "manifest_schema_version",
    "pack_root_sha256",
    "payload_root_sha256",
    "artifact_count",
    "artifact_bytes",
    "stamped_at_utc",
    "pack_id",
    "zip_path",
    "derivation",
    "derived_seed_fingerprint_sha256",
    "entropy_schema_version",
    "entropy_root_sha256",
    "seed_fingerprint_sha256",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _looks_like_windows_drive(path: str) -> bool:
    # "C:foo" and "C:\foo" patterns are ambiguous across platforms.
    return len(path) >= 2 and path[1] == ":" and path[0].isalpha()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value not allowed: {value}")


def _reject_duplicate_object_pairs(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON object key: {key}")
        out[key] = value
    return out


def _loads_strict_json_bytes(raw: bytes) -> object:
    return json.loads(
        raw.decode("utf-8"),
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_object_pairs,
    )


def _contains_terminal_control(value: str) -> bool:
    return any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in value)


def _validate_artifact_relpath(value: object) -> Optional[Path]:
    if not isinstance(value, str):
        return None
    rel = value
    if not rel or rel != rel.strip():
        return None
    if _contains_terminal_control(rel):
        return None
    # Manifest paths are POSIX-style; backslashes tend to be accidental or hostile.
    if "\\" in rel:
        return None
    if rel.startswith("/"):
        return None
    if _looks_like_windows_drive(rel):
        return None
    parts = rel.split("/")
    if any(part == "" or part in (".", "..") for part in parts):
        return None
    p = PurePosixPath(rel)
    if p.is_absolute() or p.as_posix() != rel:
        return None
    # Current pack layout requires artifacts under payload/.
    if len(parts) < 2 or parts[0] != "payload":
        return None
    return Path(*parts)


def _sha256_hex_stream(handle, *, max_bytes: Optional[int] = None) -> Tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    while True:
        read_size = 1024 * 1024 if max_bytes is None else min(1024 * 1024, int(max_bytes) - n + 1)
        chunk = handle.read(read_size)
        if not chunk:
            break
        n += len(chunk)
        if max_bytes is not None and n > max_bytes:
            raise ValueError(f"stream exceeded max_bytes ({n} > {max_bytes})")
        h.update(chunk)
    return h.hexdigest(), n


def _read_file_bytes_limited(path: Path, *, max_bytes: int) -> bytes:
    return read_trusted_bytes_limited(path, max_bytes=max_bytes)


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


def _zip_infos_limited(zf: zipfile.ZipFile, *, max_zip_members: int) -> List[zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > int(max_zip_members):
        raise ValueError(f"zip member count exceeds cap: count={len(infos)} cap={int(max_zip_members)}")
    return infos


def _validate_zip_infos(infos: Sequence[zipfile.ZipInfo]) -> List[str]:
    errors: List[str] = []
    for info in infos:
        name = str(info.filename)
        if info.is_dir():
            errors.append(f"zip directory member unsupported: {name}")
            continue
        if not name or name != name.strip() or _contains_terminal_control(name):
            errors.append(f"zip member path invalid: {name!r}")
            continue
        if "\\" in name or name.startswith("/") or _looks_like_windows_drive(name):
            errors.append(f"zip member path invalid: {name!r}")
            continue
        parts = name.split("/")
        if any(part == "" or part in (".", "..") for part in parts):
            errors.append(f"zip member path invalid: {name!r}")
            continue
        if PurePosixPath(name).as_posix() != name:
            errors.append(f"zip member path invalid: {name!r}")
            continue
        mode = (int(getattr(info, "external_attr", 0)) >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            errors.append(f"zip member is a symlink: {name}")
        elif file_type not in (0, stat.S_IFREG):
            errors.append(f"zip member type unsupported: {name}")
    return errors


def _check_payload_closure_in_dir(pack_dir: Path, *, expected: Set[str]) -> List[str]:
    payload_dir = pack_dir / "payload"
    if not payload_dir.is_dir():
        return ["missing payload directory"]
    expected_dirs: Set[str] = {"payload"}
    for rel in expected:
        parts = rel.split("/")
        for end in range(1, len(parts)):
            expected_dirs.add("/".join(parts[:end]))

    stack: List[Tuple[Path, str]] = [(payload_dir, "payload")]
    try:
        while stack:
            directory, rel_dir = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    rel = f"{rel_dir}/{entry.name}"
                    if rel in expected:
                        continue
                    if rel in expected_dirs and entry.is_dir(follow_symlinks=False):
                        stack.append((Path(entry.path), rel))
                        continue
                    return ["unexpected payload files present"]
    except OSError as exc:
        return [f"payload closure check failed: {exc}"]
    return []


def _payload_relpaths_in_zip(zf: zipfile.ZipFile) -> List[str]:
    out: List[str] = []
    for info in sorted(zf.infolist(), key=lambda x: x.filename):
        name = str(info.filename)
        if info.is_dir():
            continue
        if name == "payload" or name.startswith("payload/"):
            out.append(name)
    return out


def _non_payload_member_names_in_zip(zf: zipfile.ZipFile) -> List[str]:
    out: List[str] = []
    for info in sorted(zf.infolist(), key=lambda x: x.filename):
        name = str(info.filename)
        if info.is_dir():
            continue
        if name == "payload" or name.startswith("payload/"):
            continue
        out.append(name)
    return out


def _iter_pack_archive_files(
    pack_dir: Path,
    *,
    exclude_names: Set[str],
    skip_nested_zips: bool,
) -> List[Path]:
    include: List[Path] = []
    for p in sorted(pack_dir.rglob("*")):
        rel = p.relative_to(pack_dir).as_posix()
        if rel in exclude_names:
            continue
        if p.is_symlink():
            raise ValueError(f"refusing to archive symlink file: {rel}")
        if p.is_dir():
            continue
        if skip_nested_zips and rel.endswith(".zip"):
            continue
        include.append(p)
    return include


def _append_unexpected_payload_errors(errors: List[str], *, expected: Set[str], actual: Sequence[str]) -> None:
    extra_payload_relpaths = sorted(set(actual) - expected)
    if extra_payload_relpaths:
        preview = ", ".join(extra_payload_relpaths[:5])
        suffix = f" (+{len(extra_payload_relpaths) - 5} more)" if len(extra_payload_relpaths) > 5 else ""
        errors.append(f"unexpected payload files present: {preview}{suffix}")


def _append_unexpected_zip_member_errors(errors: List[str], *, schema_version: object, actual: Sequence[str]) -> None:
    if schema_version == MANIFEST_SCHEMA_VERSION:
        allowed = {"manifest.json", PACK_ROOT_ALIAS_FILENAME, "receipt.json"}
    elif schema_version == LEGACY_MANIFEST_SCHEMA_VERSION:
        allowed = {"manifest.json", LEGACY_ROOT_ALIAS_FILENAME, PACK_ROOT_ALIAS_FILENAME, "receipt.json"}
    else:
        return
    extra_members = sorted(set(actual) - allowed)
    if extra_members:
        preview = ", ".join(extra_members[:5])
        suffix = f" (+{len(extra_members) - 5} more)" if len(extra_members) > 5 else ""
        errors.append(f"unexpected zip members present: {preview}{suffix}")


def _output_would_self_ingest_input(input_dir: Path, out_dir: Path) -> bool:
    return input_dir == out_dir or out_dir.is_relative_to(input_dir) or input_dir.is_relative_to(out_dir)


def _require_contained_path(container: Path, candidate: Path, *, label: str) -> None:
    resolved_container = Path(container).resolve()
    resolved_parent = Path(candidate).parent.resolve()
    if not resolved_parent.is_relative_to(resolved_container):
        raise ValueError(f"{label} escapes requested output directory: {candidate}")


def _write_root_alias_files(pack_dir: Path, root_sha: str) -> None:
    _safe_write_text(pack_dir / PACK_ROOT_ALIAS_FILENAME, root_sha + "\n")


def _evidence_bundle_path_for_root(pack_dir: Path, root_sha: str) -> Path:
    return pack_dir / f"authored_evidence_{root_sha}.zip"


def _existing_evidence_bundle_path(pack_dir: Path, root_sha: str) -> Optional[Path]:
    candidate = _evidence_bundle_path_for_root(pack_dir, root_sha)
    return candidate if candidate.is_file() else None


def _finalize_public_artifacts(
    pack_dir: Path,
    *,
    receipt: Dict[str, object],
    zip_pack: bool,
    evidence_bundle: bool,
) -> Tuple[Optional[Path], Optional[Path], Optional[str]]:
    _safe_write_json(pack_dir / "receipt.json", receipt)
    candidate_result = verify_pack(pack_dir)
    if not candidate_result.ok:
        raise ValueError(
            "candidate pack directory failed verification: "
            + (candidate_result.errors[0] if candidate_result.errors else "unknown error")
        )

    zip_path: Optional[Path] = None
    if zip_pack:
        zip_path = pack_dir / "authored_pack.zip"
        _require_contained_path(pack_dir, zip_path, label="generated zip")
        _write_zip(pack_dir, zip_path)
        _verify_public_zip_projection(
            zip_path,
            expected_root=str(receipt.get("pack_root_sha256", "")),
            expected_receipt=receipt,
            generated=True,
        )

    evidence_path: Optional[Path] = None
    evidence_sha: Optional[str] = None
    if evidence_bundle:
        evidence_path, evidence_sha = write_evidence_bundle(pack_dir)
        _require_contained_path(pack_dir, evidence_path, label="generated evidence bundle")

    return zip_path, evidence_path, evidence_sha


def _verify_public_zip_projection(
    zip_path: Path,
    *,
    expected_root: str,
    expected_receipt: Dict[str, object],
    generated: bool,
) -> None:
    result = verify_pack(zip_path)
    label = "generated authored_pack.zip" if generated else "existing authored_pack.zip"
    if not result.ok:
        raise ValueError(
            f"{label} failed verification: "
            + (result.errors[0] if result.errors else "unknown error")
        )
    if result.root_sha256 != expected_root:
        raise ValueError(
            f"{label} root does not match directory pack: expected={expected_root} actual={result.root_sha256}"
        )
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            raw_receipt = _read_zip_member_bytes_limited(
                zf,
                "receipt.json",
                max_bytes=DEFAULT_MAX_MANIFEST_BYTES,
            )
        projected_receipt = _loads_strict_json_bytes(raw_receipt)
    except Exception as exc:
        raise ValueError(f"{label} receipt could not be read: {exc}") from exc
    if projected_receipt != expected_receipt:
        raise ValueError(f"{label} receipt mismatch with finalized directory receipt")


def _load_existing_receipt(pack_dir: Path) -> Dict[str, object]:
    receipt_path = pack_dir / "receipt.json"
    raw = _read_file_bytes_limited(receipt_path, max_bytes=DEFAULT_MAX_MANIFEST_BYTES)
    data = _loads_strict_json_bytes(raw)
    if not isinstance(data, dict):
        raise ValueError("existing receipt.json is not an object")
    return data


def _materialize_requested_reuse_artifacts_in_place(
    pack_dir: Path,
    *,
    receipt: Dict[str, object],
    zip_pack: bool,
    evidence_bundle: bool,
    seed_master: Optional[bytes],
) -> Tuple[Optional[Path], Optional[Path], Optional[str], Dict[str, object]]:
    updated_receipt = dict(receipt)
    receipt_path = pack_dir / "receipt.json"

    if seed_master is not None:
        _write_seed_files(pack_dir, seed_master)

    zip_path = pack_dir / "authored_pack.zip"
    if zip_path.is_symlink():
        if zip_pack:
            raise ValueError("existing authored_pack.zip is a symlink")
        zip_path = None
    elif not zip_path.is_file():
        zip_path = None
    wrote_new_zip = False
    if zip_pack:
        if zip_path is None:
            zip_path = pack_dir / "authored_pack.zip"
        if updated_receipt.get("zip_path") != "authored_pack.zip":
            updated_receipt["zip_path"] = "authored_pack.zip"
        if not zip_path.is_file():
            _write_zip(pack_dir, zip_path, receipt_override=_canonical_json_text(updated_receipt))
            wrote_new_zip = True

    if zip_path is not None and zip_path.is_file():
        _verify_public_zip_projection(
            zip_path,
            expected_root=str(updated_receipt.get("pack_root_sha256", "")),
            expected_receipt=updated_receipt,
            generated=wrote_new_zip,
        )

    if updated_receipt != receipt:
        try:
            _safe_write_json(receipt_path, updated_receipt)
        except Exception:
            if wrote_new_zip and zip_path is not None:
                try:
                    zip_path.unlink()
                except OSError:
                    pass
            raise

    root_sha = str(updated_receipt.get("pack_root_sha256", "") or "")
    evidence_path = _existing_evidence_bundle_path(pack_dir, root_sha) if root_sha else None
    evidence_sha: Optional[str] = None
    if evidence_bundle:
        evidence_valid = False
        if evidence_path is not None:
            sidecar_path = evidence_path.with_name(evidence_path.name + ".sha256")
            evidence_valid, _evidence_errors = _verify_evidence_pair(pack_dir, evidence_path, sidecar_path)
        if not evidence_valid:
            evidence_path, evidence_sha = write_evidence_bundle(pack_dir)
        else:
            evidence_sha, _ = trusted_sha256_hex(evidence_path)

    return zip_path, evidence_path, evidence_sha, updated_receipt


def _materialize_requested_reuse_artifacts(
    pack_dir: Path,
    *,
    receipt: Dict[str, object],
    zip_pack: bool,
    evidence_bundle: bool,
    seed_master: Optional[bytes] = None,
) -> Tuple[Optional[Path], Optional[Path], Optional[str], Dict[str, object]]:
    """Publish all requested reuse adjuncts as one rollback-capable operation."""
    targets: List[Path] = []
    if seed_master is not None:
        targets.extend((pack_dir / "seed_master.hex", pack_dir / "seed_master.b64"))
    if zip_pack:
        targets.extend((pack_dir / "receipt.json", pack_dir / "authored_pack.zip"))
    if evidence_bundle:
        root_sha = str(receipt.get("pack_root_sha256", "") or "")
        evidence_path = _evidence_bundle_path_for_root(pack_dir, root_sha)
        targets.extend((evidence_path, evidence_path.with_name(evidence_path.name + ".sha256")))

    unique_targets = list(dict.fromkeys(targets))
    backup_dir = Path(tempfile.mkdtemp(prefix=".ap-reuse-artifacts-", dir=str(pack_dir.parent)))
    backups: Dict[Path, Optional[Path]] = {}
    try:
        for index, target in enumerate(unique_targets):
            try:
                target_stat = os.lstat(target)
            except FileNotFoundError:
                backups[target] = None
                continue
            if not stat.S_ISREG(target_stat.st_mode):
                raise ValueError(f"reuse artifact target is not a regular file: {target}")
            backup = backup_dir / f"artifact-{index}"
            trusted_copy_with_sha256(target, backup)
            backups[target] = backup

        try:
            return _materialize_requested_reuse_artifacts_in_place(
                pack_dir,
                receipt=receipt,
                zip_pack=zip_pack,
                evidence_bundle=evidence_bundle,
                seed_master=seed_master,
            )
        except BaseException as exc:
            rollback_errors: List[str] = []
            for target, backup in backups.items():
                try:
                    try:
                        current_stat = os.lstat(target)
                    except FileNotFoundError:
                        current_stat = None
                    if current_stat is not None:
                        if stat.S_ISDIR(current_stat.st_mode) and not stat.S_ISLNK(current_stat.st_mode):
                            raise IsADirectoryError(str(target))
                        target.unlink()
                    if backup is not None and backup.exists():
                        os.replace(backup, target)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{target.name}: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    "reuse artifact publication failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _read_manifest_and_receipt(
    pack_path: Path,
    *,
    max_manifest_bytes: int,
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS,
) -> Tuple[str, Dict[str, object], Optional[Dict[str, object]]]:
    if pack_path.is_dir():
        raw_manifest = _read_file_bytes_limited(pack_path / "manifest.json", max_bytes=max_manifest_bytes)
        manifest = _loads_strict_json_bytes(raw_manifest)
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json must be an object")
        receipt: Optional[Dict[str, object]] = None
        receipt_path = pack_path / "receipt.json"
        if receipt_path.is_file():
            raw_receipt = _read_file_bytes_limited(receipt_path, max_bytes=max_manifest_bytes)
            loaded_receipt = _loads_strict_json_bytes(raw_receipt)
            if not isinstance(loaded_receipt, dict):
                raise ValueError("receipt.json must be an object")
            receipt = loaded_receipt
        return "directory", manifest, receipt

    if pack_path.is_file() and pack_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(pack_path, "r") as zf:
            _zip_infos_limited(zf, max_zip_members=int(max_zip_members))
            raw_manifest = _read_zip_member_bytes_limited(zf, "manifest.json", max_bytes=max_manifest_bytes)
            manifest = _loads_strict_json_bytes(raw_manifest)
            if not isinstance(manifest, dict):
                raise ValueError("manifest.json must be an object")
            receipt = None
            try:
                raw_receipt = _read_zip_member_bytes_limited(zf, "receipt.json", max_bytes=max_manifest_bytes)
            except KeyError:
                raw_receipt = None
            if raw_receipt is not None:
                loaded_receipt = _loads_strict_json_bytes(raw_receipt)
                if not isinstance(loaded_receipt, dict):
                    raise ValueError("receipt.json must be an object")
                receipt = loaded_receipt
            return "zip", manifest, receipt

    raise ValueError(f"unsupported pack path: {pack_path}")


def inspect_pack(
    pack_path: Path,
    *,
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS,
    artifact_preview_limit: int = 20,
) -> Dict[str, object]:
    pack_path = Path(pack_path).resolve()
    verify_result = verify_pack(
        pack_path,
        max_manifest_bytes=int(max_manifest_bytes),
        max_artifact_bytes=int(max_artifact_bytes),
        max_total_bytes=int(max_total_bytes),
        max_zip_members=int(max_zip_members),
    )
    if not verify_result.ok:
        pack_type = "zip" if pack_path.is_file() and pack_path.suffix.lower() == ".zip" else "directory"
        return {
            "inspected_path": str(pack_path),
            "pack_type": pack_type,
            "pack_root_sha256": verify_result.root_sha256,
            "payload_root_sha256": verify_result.payload_root_sha256,
            "verification_ok": False,
            "verification_errors": list(verify_result.errors),
            "artifact_count_verified": int(verify_result.file_count),
            "artifact_bytes_verified": int(verify_result.total_bytes),
        }
    pack_type, manifest, receipt = _read_manifest_and_receipt(
        pack_path,
        max_manifest_bytes=int(max_manifest_bytes),
        max_zip_members=int(max_zip_members),
    )

    artifacts_obj = manifest.get("artifacts")
    if not isinstance(artifacts_obj, list):
        raise ValueError("manifest.artifacts missing or invalid")

    preview_limit = max(0, int(artifact_preview_limit))
    artifact_preview: List[Dict[str, object]] = []
    artifact_bytes = 0
    for item in artifacts_obj:
        if not isinstance(item, dict):
            continue
        size = item.get("size_bytes")
        if isinstance(size, int) and size >= 0:
            artifact_bytes += int(size)
        if len(artifact_preview) >= preview_limit:
            continue
        preview_item: Dict[str, object] = {}
        path = item.get("path")
        sha = item.get("sha256")
        if isinstance(path, str):
            preview_item["path"] = path
        if isinstance(size, int):
            preview_item["size_bytes"] = int(size)
        if isinstance(sha, str):
            preview_item["sha256"] = sha
        if preview_item:
            artifact_preview.append(preview_item)

    has_zip = False
    has_evidence_bundle = False
    if pack_type == "directory":
        has_zip = (pack_path / "authored_pack.zip").is_file()
        has_evidence_bundle = any(pack_path.glob("authored_evidence_*.zip"))
    else:
        has_zip = True
        has_evidence_bundle = False

    receipt_summary: Optional[Dict[str, object]] = None
    if isinstance(receipt, dict):
        receipt_summary = {}
        for key in (
            "schema_version",
            "tool",
            "tool_version",
            "pack_layout",
            "stamped_at_utc",
            "artifact_count",
            "artifact_bytes",
        ):
            value = receipt.get(key)
            if value is not None:
                receipt_summary[key] = value
        if isinstance(receipt.get("derivation"), dict):
            receipt_summary["derivation"] = dict(receipt["derivation"])
        if "authored_sources_audit_status" in receipt:
            receipt_summary["authored_sources_audit_status"] = receipt.get("authored_sources_audit_status")

    summary: Dict[str, object] = {
        "inspected_path": str(pack_path),
        "pack_type": pack_type,
        "pack_root_sha256": manifest_root_sha256(manifest),
        "payload_root_sha256": str(manifest.get("payload_root_sha256", "")),
        "manifest_schema_version": str(manifest.get("schema_version", "")),
        "artifact_count": len(artifacts_obj),
        "artifact_bytes": int(artifact_bytes),
        "artifact_preview": artifact_preview,
        "artifact_preview_truncated": len(artifacts_obj) > len(artifact_preview),
        "has_receipt": isinstance(receipt, dict),
        "has_zip": bool(has_zip),
        "has_evidence_bundle": bool(has_evidence_bundle),
        "verification_ok": bool(verify_result.ok),
        "verification_errors": list(verify_result.errors),
        "artifact_count_verified": int(verify_result.file_count),
        "artifact_bytes_verified": int(verify_result.total_bytes),
    }
    if isinstance(manifest.get("pack_id"), str):
        summary["pack_id"] = manifest["pack_id"]
    if isinstance(manifest.get("derivation"), dict):
        summary["derivation"] = dict(manifest["derivation"])
    if receipt_summary is not None:
        summary["receipt_summary"] = receipt_summary
    return summary


def _verify_one_artifact_in_dir(pack_dir: Path, *, idx: int, rel_s: str, size: int, sha: str) -> Optional[str]:
    rel_path = Path(rel_s)
    target = pack_dir / rel_path
    # Guard against path traversal and symlink escapes.
    try:
        resolved = target.resolve()
    except Exception:
        resolved = target
    if not resolved.is_relative_to(pack_dir):
        return f"artifact[{idx}] path escapes pack dir: {rel_s}"
    if target.is_symlink():
        return f"artifact[{idx}] is a symlink (refusing): {rel_s}"
    if not target.is_file():
        return f"missing artifact file: {rel_s}"
    try:
        actual_sha, n = trusted_sha256_hex(target, max_bytes=size)
    except Exception as exc:
        try:
            actual_size = target.stat().st_size
        except Exception:
            actual_size = "unknown"
        if isinstance(actual_size, int) and actual_size != size:
            return f"size mismatch: {rel_s} expected={size} actual={actual_size}"
        return f"failed to read artifact: {rel_s}: {exc}"
    if n != size:
        return f"size mismatch: {rel_s} expected={size} actual={n}"
    if actual_sha != sha:
        return f"sha256 mismatch: {rel_s}"
    return None


def _verify_one_artifact_in_zip(zf: zipfile.ZipFile, *, idx: int, rel_s: str, size: int, sha: str) -> Optional[str]:
    try:
        info = zf.getinfo(rel_s)
    except KeyError:
        return f"missing artifact file in zip: {rel_s}"
    if info.is_dir():
        return f"artifact[{idx}] is a directory in zip: {rel_s}"
    mode = (int(getattr(info, "external_attr", 0)) >> 16) & 0xFFFF
    if stat.S_IFMT(mode) == stat.S_IFLNK:
        return f"artifact[{idx}] is a symlink in zip: {rel_s}"
    zip_size = int(getattr(info, "file_size", -1))
    if zip_size != size:
        return f"size mismatch: {rel_s} expected={size} actual={zip_size}"
    try:
        with zf.open(info, "r") as handle:
            actual_sha, n = _sha256_hex_stream(handle, max_bytes=size)
    except Exception as exc:
        return f"failed to read artifact in zip: {rel_s}: {exc}"
    if n != size:
        return f"size mismatch: {rel_s} expected={size} actual={n}"
    if actual_sha != sha:
        return f"sha256 mismatch: {rel_s}"
    return None


def _verify_manifest_artifacts(
    artifacts: object,
    *,
    max_artifact_bytes: int,
    max_total_bytes: int,
    verify_one: Callable[[int, str, int, str], Optional[str]],
) -> Tuple[int, int, Set[str], List[str]]:
    errors: List[str] = []
    file_count = 0
    total_bytes = 0
    declared_total_bytes = 0
    expected_payload_relpaths: Set[str] = set()

    if not isinstance(artifacts, list) or not artifacts:
        errors.append("manifest.artifacts missing or empty")
        return file_count, total_bytes, expected_payload_relpaths, errors

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
        if rel_s in expected_payload_relpaths:
            errors.append(f"duplicate artifact path: {rel_s}")
            continue
        expected_payload_relpaths.add(rel_s)
        if not isinstance(sha, str) or len(sha) != 64:
            errors.append(f"artifact[{i}].sha256 invalid")
            continue
        if type(size) is not int or size < 0:
            errors.append(f"artifact[{i}].size_bytes invalid")
            continue
        if size > max_artifact_bytes:
            errors.append(f"artifact[{i}] too large: {rel_s} size_bytes={size} cap={max_artifact_bytes}")
            continue
        if declared_total_bytes + int(size) > max_total_bytes:
            errors.append(f"pack too large (cap exceeded): cap={max_total_bytes}")
            continue
        declared_total_bytes += int(size)

        err = verify_one(i, rel_s, int(size), str(sha))
        if err is not None:
            errors.append(err)
            continue

        file_count += 1
        total_bytes += int(size)

    return file_count, total_bytes, expected_payload_relpaths, errors


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _default_public_file_mode(path: Path) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        current_umask = os.umask(0)
        os.umask(current_umask)
        return 0o666 & ~current_umask


def _atomic_write_text(path: Path, content: str, *, mode: int) -> None:
    _ensure_parent(path)
    fd, tmp_name = tempfile.mkstemp(prefix=".ap-write-", dir=str(path.parent), text=True)
    tmp_path = Path(tmp_name)
    try:
        try:
            os.fchmod(fd, mode)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _safe_write_text(path: Path, content: str) -> None:
    _atomic_write_text(path, content, mode=_default_public_file_mode(path))


def _safe_write_private_text(path: Path, content: str) -> None:
    _atomic_write_text(path, content, mode=0o600)


def _write_seed_files(pack_dir: Path, seed_master: bytes) -> None:
    seed_hex = seed_master.hex()
    seed_b64 = base64.b64encode(seed_master).decode("ascii")
    _safe_write_private_text(pack_dir / "seed_master.hex", seed_hex + "\n")
    _safe_write_private_text(pack_dir / "seed_master.b64", seed_b64 + "\n")


def _canonical_json_text(obj: Dict[str, object]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _safe_write_json(path: Path, obj: Dict[str, object]) -> None:
    _atomic_write_text(path, _canonical_json_text(obj), mode=_default_public_file_mode(path))


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

        dst_rel = Path("payload") / Path(src_rel)
        dst = pack_dir / dst_rel

        expected_sha = str(a.get("sha256", ""))
        expected_size = int(a.get("size_bytes", 0))
        actual_sha, actual_size = trusted_copy_with_sha256(src, dst)
        if actual_sha != expected_sha or actual_size != expected_size:
            raise ValueError(f"artifact copy diverged from source bytes: {src_rel}")

        out.append(
            {
                "path": dst_rel.as_posix(),
                "sha256": expected_sha,
                "size_bytes": expected_size,
            }
        )
    out.sort(key=lambda d: str(d.get("path", "")))
    return out


def _copy_source_record(source_dir: Path, candidate_dir: Path) -> Path:
    source_dir = Path(source_dir).resolve()
    if not source_dir.is_dir():
        raise ValueError(f"source record must be a directory: {source_dir}")
    target_dir = candidate_dir / "authored_sources"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    copied = 0
    for dirpath, dirnames, filenames in os.walk(source_dir, onerror=lambda exc: (_ for _ in ()).throw(exc)):
        dirnames.sort()
        filenames.sort()
        base = Path(dirpath)
        for name in filenames:
            src = base / name
            rel = src.relative_to(source_dir).as_posix()
            if _contains_terminal_control(rel) or "\\" in rel or rel != rel.strip():
                raise ValueError(f"source record path is not canonical POSIX text: {rel!r}")
            parts = rel.split("/")
            if any(part == "" or part in (".", "..") for part in parts):
                raise ValueError(f"source record path is not canonical POSIX text: {rel!r}")
            trusted_copy_with_sha256(src, target_dir / Path(*parts))
            copied += 1
    if copied == 0:
        raise ValueError("source record directory contains no files")
    return target_dir


def _merge_receipt_extra_fields(receipt: Dict[str, object], extra_fields: Optional[Dict[str, object]]) -> None:
    if not extra_fields:
        return
    if not isinstance(extra_fields, dict):
        raise ValueError("source record receipt fields must be a dict or None")
    reserved = sorted(RESERVED_RECEIPT_KEYS.intersection(extra_fields))
    if reserved:
        names = ", ".join(reserved)
        raise ValueError(f"source record receipt fields contain reserved field(s): {names}")
    receipt.update(dict(extra_fields))


def _publish_replacement_directory(pack_dir: Path, candidate_dir: Path) -> None:
    backup_dir = pack_dir.parent / f".ap-reuse-backup-{pack_dir.name}"
    if backup_dir.exists():
        raise FileExistsError(f"reuse backup path already exists: {backup_dir}")
    pack_dir.rename(backup_dir)
    try:
        candidate_dir.replace(pack_dir)
    except Exception:
        if not pack_dir.exists() and backup_dir.exists():
            backup_dir.rename(pack_dir)
        raise
    try:
        shutil.rmtree(backup_dir)
    except Exception:
        pass


def derive_seed_master(
    *,
    root_sha256_hex: str,
    derivation_version: str = DEFAULT_DERIVATION_VERSION,
    authored_sources_sha256_hex: Optional[str] = None,
) -> bytes:
    """
    Derive the 32-byte seed_master.

    Backwards compatible behavior:
    - If authored_sources_sha256_hex is None: identical to the v1 derivation (root-only).
    - If provided: mix the sources hash into the HKDF salt, producing a different seed.
    """
    root_bytes = bytes.fromhex(root_sha256_hex)
    info = derivation_version.encode("utf-8")
    salt = b"AUTHOREDPACK-SALT-v1"
    if authored_sources_sha256_hex:
        src_raw = str(authored_sources_sha256_hex)
        try:
            src = bytes.fromhex(src_raw)
        except Exception as exc:
            raise ValueError("invalid authored_sources_sha256_hex") from exc
        if len(src) != 32:
            raise ValueError("authored_sources_sha256_hex must decode to 32 bytes")
        # Salt-mixing keeps root as the IKM, and makes the additional sources explicit.
        salt = b"AUTHOREDPACK-SALT-v2" + src
    return hkdf_sha256(ikm=root_bytes, length=32, salt=salt, info=info)


def seed_fingerprint_sha256(seed_master: bytes) -> str:
    return sha256_hex(bytes(seed_master))


def _pack_dir_for_root(out_dir: Path, root_sha256: str) -> Path:
    return out_dir / root_sha256


def _is_sha256_hex(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value.lower())


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z") or value != value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _validate_derivation_metadata(value: object, *, owner: str) -> List[str]:
    if not isinstance(value, dict):
        return [f"{owner} derivation invalid"]
    errors: List[str] = []
    if value.get("method") != "hkdf-sha256":
        errors.append(f"{owner} derivation.method invalid")
    version = value.get("derivation_version")
    if not isinstance(version, str) or not version:
        errors.append(f"{owner} derivation.derivation_version invalid")
    mode = value.get("mode")
    if mode not in {"root-only", "root-plus-sources"}:
        errors.append(f"{owner} derivation.mode invalid")
    sources_sha = value.get("authored_sources_sha256")
    if mode == "root-plus-sources":
        if not _is_sha256_hex(sources_sha):
            errors.append(f"{owner} derivation.authored_sources_sha256 invalid")
    elif sources_sha is not None:
        errors.append(f"{owner} derivation.authored_sources_sha256 not allowed")
    return errors


def _validate_manifest_structure(manifest: object) -> Tuple[List[Dict[str, object]], List[str]]:
    if not isinstance(manifest, dict):
        return [], ["manifest.json must be an object"]
    errors: List[str] = []
    schema_version = manifest.get("schema_version")
    if schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
        return [], ["manifest schema_version unsupported"]

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return [], ["manifest.artifacts missing or empty"]

    validated: List[Dict[str, object]] = []
    seen_paths: Set[str] = set()
    for i, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"artifact[{i}] not an object")
            continue
        for field in ("path", "sha256", "size_bytes"):
            if field not in artifact:
                errors.append(f"artifact[{i}].{field} missing")
        if any(field not in artifact for field in ("path", "sha256", "size_bytes")):
            continue
        rel_path = _validate_artifact_relpath(artifact.get("path"))
        if rel_path is None:
            errors.append(f"artifact[{i}].path invalid")
        else:
            rel_s = rel_path.as_posix()
            if rel_s in seen_paths:
                errors.append(f"duplicate artifact path: {rel_s}")
            seen_paths.add(rel_s)
        sha = artifact.get("sha256")
        if not _is_sha256_hex(sha):
            errors.append(f"artifact[{i}].sha256 invalid")
        size = artifact.get("size_bytes")
        if type(size) is not int or int(size) < 0:
            errors.append(f"artifact[{i}].size_bytes invalid")
        if rel_path is not None and _is_sha256_hex(sha) and type(size) is int and int(size) >= 0:
            validated.append({"path": rel_path.as_posix(), "sha256": str(sha), "size_bytes": int(size)})

    if schema_version == MANIFEST_SCHEMA_VERSION:
        if "payload_root_sha256" not in manifest:
            errors.append("manifest payload_root_sha256 missing")
        elif not _is_sha256_hex(manifest.get("payload_root_sha256")):
            errors.append("manifest payload_root_sha256 invalid")
        pack_id = manifest.get("pack_id")
        if pack_id is not None and (not isinstance(pack_id, str) or not pack_id):
            errors.append("manifest pack_id invalid")
        notes = manifest.get("notes")
        if notes is not None and not isinstance(notes, str):
            errors.append("manifest notes invalid")
        created_at = manifest.get("created_at_utc")
        if created_at is not None and not _is_utc_timestamp(created_at):
            errors.append("manifest created_at_utc invalid")
        dice = manifest.get("dice")
        if dice is not None:
            if not isinstance(dice, list):
                errors.append("manifest dice invalid")
            else:
                for i, die in enumerate(dice):
                    if not isinstance(die, dict):
                        errors.append(f"manifest dice[{i}] invalid")
                        continue
                    if not isinstance(die.get("die"), str) or not str(die.get("die")):
                        errors.append(f"manifest dice[{i}].die invalid")
                    if type(die.get("value")) is not int:
                        errors.append(f"manifest dice[{i}].value invalid")
        derivation = manifest.get("derivation")
        if derivation is not None:
            errors.extend(_validate_derivation_metadata(derivation, owner="manifest"))
    return validated, errors


def _validate_root_alias_bytes(raw_expected: bytes, *, name: str, root_sha: str) -> List[str]:
    try:
        expected = raw_expected.decode("utf-8").strip()
    except UnicodeDecodeError:
        return [f"{name} is not valid UTF-8"]
    if not _is_sha256_hex(expected):
        return [f"{name} must be a 64-character hexadecimal SHA-256 digest"]
    if expected.lower() != root_sha.lower():
        return [f"{name} does not match manifest root"]
    return []


def _validate_manifest_payload_root(
    manifest: Dict[str, object], artifact_entries: Sequence[Dict[str, object]]
) -> Tuple[str, List[str]]:
    errors: List[str] = []
    payload_root = manifest.get("payload_root_sha256")
    if payload_root is None:
        return "", errors
    if not _is_sha256_hex(payload_root):
        return "", ["manifest payload_root_sha256 invalid"]
    computed = payload_root_sha256(artifact_entries)
    if str(payload_root) != computed:
        errors.append("manifest payload_root_sha256 mismatch")
    return str(payload_root), errors


def _build_derivation_metadata(
    *,
    derive_seed: bool,
    authored_sources_sha256: Optional[str],
) -> Optional[Dict[str, object]]:
    if not derive_seed:
        return None
    derivation: Dict[str, object] = {
        "method": "hkdf-sha256",
        "derivation_version": DEFAULT_DERIVATION_VERSION,
        "mode": "root-only",
    }
    if authored_sources_sha256:
        derivation["mode"] = "root-plus-sources"
        derivation["authored_sources_sha256"] = str(authored_sources_sha256)
    return derivation


def _read_root_alias_file(path: Path, *, name: str, root_sha: str) -> List[str]:
    if path.is_symlink():
        return [f"{name} is a symlink"]
    if not path.is_file():
        return [f"{name} is not a file"]
    try:
        raw_expected = _read_file_bytes_limited(path, max_bytes=256)
    except Exception as exc:
        return [f"{name} could not be read: {exc}"]
    return _validate_root_alias_bytes(raw_expected, name=name, root_sha=root_sha)


def _validate_current_root_alias_in_dir(pack_dir: Path, *, root_sha: str) -> List[str]:
    errors: List[str] = []
    legacy_path = pack_dir / LEGACY_ROOT_ALIAS_FILENAME
    if legacy_path.exists():
        errors.append(f"unexpected {LEGACY_ROOT_ALIAS_FILENAME} in {MANIFEST_SCHEMA_VERSION} pack")
    current_path = pack_dir / PACK_ROOT_ALIAS_FILENAME
    if not current_path.exists():
        errors.append(f"missing {PACK_ROOT_ALIAS_FILENAME}")
        return errors
    errors.extend(_read_root_alias_file(current_path, name=PACK_ROOT_ALIAS_FILENAME, root_sha=root_sha))
    return errors


def _validate_legacy_root_alias_in_dir(pack_dir: Path, *, root_sha: str) -> List[str]:
    legacy_path = pack_dir / LEGACY_ROOT_ALIAS_FILENAME
    if not legacy_path.exists():
        return [f"missing {LEGACY_ROOT_ALIAS_FILENAME}"]
    errors = _read_root_alias_file(legacy_path, name=LEGACY_ROOT_ALIAS_FILENAME, root_sha=root_sha)
    current_path = pack_dir / PACK_ROOT_ALIAS_FILENAME
    if current_path.exists():
        errors.extend(_read_root_alias_file(current_path, name=PACK_ROOT_ALIAS_FILENAME, root_sha=root_sha))
    return errors


def _read_root_alias_zip_member(zf: zipfile.ZipFile, *, name: str, root_sha: str) -> List[str]:
    try:
        raw_expected = _read_zip_member_bytes_limited(zf, name, max_bytes=256)
    except KeyError:
        return [f"missing {name} in zip"]
    except Exception as exc:
        return [f"{name} could not be read from zip: {exc}"]
    return _validate_root_alias_bytes(raw_expected, name=name, root_sha=root_sha)


def _zip_has_member(zf: zipfile.ZipFile, name: str) -> bool:
    try:
        zf.getinfo(name)
    except KeyError:
        return False
    return True


def _validate_current_root_alias_in_zip(zf: zipfile.ZipFile, *, root_sha: str) -> List[str]:
    errors: List[str] = []
    if _zip_has_member(zf, LEGACY_ROOT_ALIAS_FILENAME):
        errors.append(f"unexpected {LEGACY_ROOT_ALIAS_FILENAME} in {MANIFEST_SCHEMA_VERSION} zip")
    errors.extend(_read_root_alias_zip_member(zf, name=PACK_ROOT_ALIAS_FILENAME, root_sha=root_sha))
    return errors


def _validate_legacy_root_alias_in_zip(zf: zipfile.ZipFile, *, root_sha: str) -> List[str]:
    errors = _read_root_alias_zip_member(zf, name=LEGACY_ROOT_ALIAS_FILENAME, root_sha=root_sha)
    if _zip_has_member(zf, PACK_ROOT_ALIAS_FILENAME):
        errors.extend(_read_root_alias_zip_member(zf, name=PACK_ROOT_ALIAS_FILENAME, root_sha=root_sha))
    return errors


def _validate_current_receipt(
    receipt: object,
    *,
    manifest: Dict[str, object],
    root_sha: str,
    artifact_entries: Sequence[Dict[str, object]],
    expect_zip_projection: bool = False,
) -> List[str]:
    errors: List[str] = []
    if not isinstance(receipt, dict):
        return ["receipt.json must be an object"]
    required_fields = (
        "schema_version",
        "tool",
        "tool_version",
        "pack_layout",
        "manifest_schema_version",
        "pack_root_sha256",
        "payload_root_sha256",
        "artifact_count",
        "artifact_bytes",
        "stamped_at_utc",
    )
    for field in required_fields:
        if field not in receipt:
            errors.append(f"receipt.json missing {field}")
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        errors.append("receipt.json schema_version invalid")
    if receipt.get("tool") != "authored-pack":
        errors.append("receipt.json tool invalid")
    if not isinstance(receipt.get("tool_version"), str) or not str(receipt.get("tool_version")):
        errors.append("receipt.json tool_version invalid")
    if receipt.get("manifest_schema_version") != manifest.get("schema_version"):
        errors.append("receipt.json manifest_schema_version mismatch")
    if receipt.get("pack_layout") != PACK_LAYOUT_VERSION:
        errors.append("receipt.json pack_layout mismatch")
    if receipt.get("entropy_schema_version") is not None:
        errors.append("receipt.json entropy_schema_version not allowed")
    if receipt.get("entropy_root_sha256") is not None:
        errors.append("receipt.json entropy_root_sha256 not allowed")
    if receipt.get("seed_fingerprint_sha256") is not None:
        errors.append("receipt.json seed_fingerprint_sha256 not allowed")
    pack_root = receipt.get("pack_root_sha256")
    if pack_root is None:
        errors.append("receipt.json missing pack_root_sha256")
    elif pack_root != root_sha:
        errors.append("receipt.json pack_root_sha256 mismatch")
    payload_root = manifest.get("payload_root_sha256")
    if receipt.get("payload_root_sha256") != payload_root:
        errors.append("receipt.json payload_root_sha256 mismatch")
    artifact_count = receipt.get("artifact_count")
    if type(artifact_count) is not int or int(artifact_count) < 0:
        errors.append("receipt.json artifact_count invalid")
    elif artifact_count != len(artifact_entries):
        errors.append("receipt.json artifact_count mismatch")
    total_bytes = sum(int(a.get("size_bytes", 0) or 0) for a in artifact_entries)
    artifact_bytes = receipt.get("artifact_bytes")
    if type(artifact_bytes) is not int or int(artifact_bytes) < 0:
        errors.append("receipt.json artifact_bytes invalid")
    elif artifact_bytes != total_bytes:
        errors.append("receipt.json artifact_bytes mismatch")

    manifest_pack_id = manifest.get("pack_id")
    if manifest_pack_id is None:
        if receipt.get("pack_id") is not None:
            errors.append("receipt.json pack_id present without manifest pack_id")
    elif receipt.get("pack_id") != manifest_pack_id:
        errors.append("receipt.json pack_id mismatch")
    if not _is_utc_timestamp(receipt.get("stamped_at_utc")):
        errors.append("receipt.json stamped_at_utc invalid")
    zip_path = receipt.get("zip_path")
    if expect_zip_projection and zip_path is None:
        errors.append("receipt.json missing zip_path")
    elif zip_path is not None and zip_path != "authored_pack.zip":
        errors.append("receipt.json zip_path invalid")

    manifest_derivation = manifest.get("derivation")
    receipt_derivation = receipt.get("derivation")
    if manifest_derivation is None:
        if receipt_derivation is not None:
            errors.append("receipt.json derivation present without manifest derivation")
        if receipt.get("derived_seed_fingerprint_sha256") is not None:
            errors.append("receipt.json derived_seed_fingerprint_sha256 present without manifest derivation")
    else:
        if receipt_derivation != manifest_derivation:
            errors.append("receipt.json derivation mismatch")
        value = receipt.get("derived_seed_fingerprint_sha256")
        if value is None:
            errors.append("receipt.json missing derived_seed_fingerprint_sha256")
        elif not _is_sha256_hex(value):
            errors.append("receipt.json derived_seed_fingerprint_sha256 invalid")
        elif isinstance(manifest_derivation, dict):
            try:
                expected_seed = derive_seed_master(
                    root_sha256_hex=root_sha,
                    derivation_version=str(manifest_derivation.get("derivation_version", "")),
                    authored_sources_sha256_hex=(
                        str(manifest_derivation.get("authored_sources_sha256"))
                        if manifest_derivation.get("mode") == "root-plus-sources"
                        else None
                    ),
                )
                if value != seed_fingerprint_sha256(expected_seed):
                    errors.append("receipt.json derived_seed_fingerprint_sha256 mismatch")
            except Exception as exc:
                errors.append(f"receipt.json derivation invalid: {exc}")
    return errors


@dataclass(frozen=True)
class AssembleResult:
    pack_dir: Path
    root_sha256: str
    payload_root_sha256: str
    receipt: Dict[str, object]
    seed_master: Optional[bytes] = None
    zip_path: Optional[Path] = None
    evidence_bundle_path: Optional[Path] = None
    evidence_bundle_sha256: Optional[str] = None

    @property
    def pack_root_sha256(self) -> str:
        return self.root_sha256


def assemble_pack(
    *,
    input_dir: Path,
    out_dir: Path,
    pack_id: Optional[str] = None,
    notes: Optional[str] = None,
    created_at_utc: Optional[str] = None,
    dice: Optional[Sequence[Tuple[str, int]]] = None,
    include_hidden: bool = False,
    exclude_relpaths: Optional[Sequence[str]] = None,
    zip_pack: bool = False,
    derive_seed: bool = False,
    authored_sources_sha256: Optional[str] = None,
    evidence_bundle: bool = False,
    write_seed_files: bool = False,
    print_seed: bool = False,
    source_record_dir: Optional[Path] = None,
    source_record_receipt_fields: Optional[Dict[str, object]] = None,
) -> AssembleResult:
    input_dir = Path(input_dir).resolve()
    out_dir = Path(out_dir).resolve()
    if not input_dir.is_dir():
        raise ValueError(f"--input must be a directory: {input_dir}")
    if _output_would_self_ingest_input(input_dir, out_dir):
        raise ValueError("--input and --out must not overlap")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_artifacts = collect_artifacts(input_dir, include_hidden=include_hidden, exclude_relpaths=exclude_relpaths)
    if not raw_artifacts:
        raise ValueError("input directory contains no artifacts")
    for index, artifact in enumerate(raw_artifacts):
        source_rel = artifact.get("source_relpath")
        if not isinstance(source_rel, str) or _validate_artifact_relpath(f"payload/{source_rel}") is None:
            raise ValueError(f"input artifact path is not canonical POSIX text: artifact[{index}]")

    # Create a unique temp pack dir to avoid partially-written packs and timestamp collisions.
    tmp_dir = Path(tempfile.mkdtemp(prefix=".authored_pack_tmp_", dir=str(out_dir)))

    try:
        artifact_entries = _copy_payload_files(input_dir=input_dir, pack_dir=tmp_dir, artifacts=raw_artifacts)
        payload_root = payload_root_sha256(artifact_entries)
        derivation = _build_derivation_metadata(
            derive_seed=bool(derive_seed),
            authored_sources_sha256=authored_sources_sha256,
        )
        manifest = build_manifest(
            pack_id=pack_id,
            artifacts=artifact_entries,
            payload_root_sha256=payload_root,
            notes=notes,
            created_at_utc=created_at_utc,
            dice=dice,
            derivation=derivation,
        )
        root_sha = manifest_root_sha256(manifest)
        seed_master: Optional[bytes] = None
        if derive_seed:
            seed_master = derive_seed_master(
                root_sha256_hex=root_sha,
                authored_sources_sha256_hex=authored_sources_sha256,
            )

        pack_dir = _pack_dir_for_root(out_dir, root_sha)
        _require_contained_path(out_dir, pack_dir, label="content-addressed pack path")
        try:
            pack_stat = os.lstat(pack_dir)
        except FileNotFoundError:
            pack_stat = None
        except OSError as exc:
            raise ValueError(f"failed to inspect content-addressed pack path: {exc}") from exc
        if pack_stat is not None and stat.S_ISLNK(pack_stat.st_mode):
            raise ValueError(f"content-addressed pack path is a symlink: {pack_dir}")
        if pack_stat is not None and not stat.S_ISDIR(pack_stat.st_mode):
            raise FileExistsError(f"content-addressed pack path is not a directory: {pack_dir}")
        if pack_stat is not None:
            # Idempotent behavior: if the existing pack matches the manifest root, reuse it.
            existing_manifest = pack_dir / "manifest.json"
            if existing_manifest.is_file():
                existing = None
                try:
                    raw = _read_file_bytes_limited(existing_manifest, max_bytes=DEFAULT_MAX_MANIFEST_BYTES)
                    existing = _loads_strict_json_bytes(raw)
                except Exception:
                    existing = None
                if isinstance(existing, dict) and manifest_root_sha256(existing) == root_sha:
                    strict = verify_pack(pack_dir)
                    if not strict.ok:
                        raise ValueError(
                            "existing pack failed verification: "
                            + (strict.errors[0] if strict.errors else "unknown error")
                        )
                    if source_record_dir is not None or source_record_receipt_fields is not None:
                        receipt = _load_existing_receipt(pack_dir)
                        _safe_write_json(tmp_dir / "manifest.json", manifest)
                        _write_root_alias_files(tmp_dir, root_sha)
                        if source_record_dir is not None:
                            _copy_source_record(source_record_dir, tmp_dir)
                        elif (pack_dir / "authored_sources").is_dir():
                            _copy_source_record(pack_dir / "authored_sources", tmp_dir)
                        if seed_master is not None and (
                            write_seed_files
                            or (pack_dir / "seed_master.hex").is_file()
                            or (pack_dir / "seed_master.b64").is_file()
                        ):
                            _write_seed_files(tmp_dir, seed_master)
                        receipt = dict(receipt)
                        _merge_receipt_extra_fields(receipt, source_record_receipt_fields)
                        want_zip = bool(zip_pack or (pack_dir / "authored_pack.zip").is_file())
                        if want_zip:
                            receipt["zip_path"] = "authored_pack.zip"
                        want_evidence = bool(evidence_bundle or _existing_evidence_bundle_path(pack_dir, root_sha))
                        zip_path_tmp, evidence_path_tmp, evidence_sha = _finalize_public_artifacts(
                            tmp_dir,
                            receipt=receipt,
                            zip_pack=want_zip,
                            evidence_bundle=want_evidence,
                        )
                        _publish_replacement_directory(pack_dir, tmp_dir)
                        if print_seed and seed_master is not None:
                            _print_seed_material(seed_master)
                        return AssembleResult(
                            pack_dir=pack_dir,
                            root_sha256=root_sha,
                            payload_root_sha256=payload_root,
                            receipt=receipt,
                            seed_master=seed_master,
                            zip_path=(pack_dir / zip_path_tmp.name) if zip_path_tmp is not None else None,
                            evidence_bundle_path=(
                                pack_dir / evidence_path_tmp.name if evidence_path_tmp is not None else None
                            ),
                            evidence_bundle_sha256=evidence_sha,
                        )
                    receipt = _load_existing_receipt(pack_dir)
                    existing_zip_path, ev_path, ev_sha, receipt = _materialize_requested_reuse_artifacts(
                        pack_dir,
                        receipt=receipt,
                        zip_pack=bool(zip_pack),
                        evidence_bundle=bool(evidence_bundle),
                        seed_master=(seed_master if write_seed_files else None),
                    )
                    if existing_zip_path is not None and existing_zip_path.is_file():
                        zip_res = verify_pack(existing_zip_path)
                        if not zip_res.ok:
                            raise ValueError(
                                "existing authored_pack.zip failed verification: "
                                + (zip_res.errors[0] if zip_res.errors else "unknown error")
                            )
                    if print_seed and seed_master is not None:
                        _print_seed_material(seed_master)
                    try:
                        shutil.rmtree(tmp_dir)
                    except Exception:
                        pass
                    return AssembleResult(
                        pack_dir=pack_dir,
                        root_sha256=root_sha,
                        payload_root_sha256=payload_root,
                        receipt=dict(receipt),
                        seed_master=seed_master,
                        zip_path=existing_zip_path,
                        evidence_bundle_path=ev_path,
                        evidence_bundle_sha256=ev_sha,
                    )
            raise FileExistsError(f"pack already exists with different contents: {pack_dir}")

        # Write pack contents into temp dir first.
        _safe_write_json(tmp_dir / "manifest.json", manifest)
        _write_root_alias_files(tmp_dir, root_sha)

        if write_seed_files and seed_master is not None:
            _write_seed_files(tmp_dir, seed_master)

        if source_record_dir is not None:
            _copy_source_record(source_record_dir, tmp_dir)

        receipt = _build_receipt(
            root_sha256=root_sha,
            payload_root_sha256=payload_root,
            pack_id=pack_id,
            artifact_entries=artifact_entries,
            zip_path=Path("authored_pack.zip") if zip_pack else None,
            derivation=derivation,
            seed_master=seed_master,
            extra_fields=source_record_receipt_fields,
        )
        zip_path_tmp, ev_path_tmp, ev_sha = _finalize_public_artifacts(
            tmp_dir,
            receipt=receipt,
            zip_pack=bool(zip_pack),
            evidence_bundle=bool(evidence_bundle),
        )

        # Atomic-ish move: rename tmp dir into content-addressed target.
        _require_contained_path(out_dir, tmp_dir, label="temporary pack path")
        tmp_dir.replace(pack_dir)

        if print_seed and seed_master is not None:
            _print_seed_material(seed_master)

        zip_path = pack_dir / zip_path_tmp.name if zip_path_tmp is not None else None
        ev_path = pack_dir / ev_path_tmp.name if ev_path_tmp is not None else None
        return AssembleResult(
            pack_dir=pack_dir,
            root_sha256=root_sha,
            payload_root_sha256=payload_root,
            receipt=receipt,
            seed_master=seed_master,
            zip_path=zip_path,
            evidence_bundle_path=ev_path,
            evidence_bundle_sha256=ev_sha,
        )
    except Exception:
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass
        raise


# Python compatibility aliases for older imports. Public CLI verbs already lead
# with assemble/consume-bin; these aliases keep downstream Python callers stable.
StampResult = AssembleResult
stamp_pack = assemble_pack


def _build_receipt(
    *,
    root_sha256: str,
    payload_root_sha256: str,
    pack_id: Optional[str],
    artifact_entries: Sequence[Dict[str, object]],
    zip_path: Optional[Path],
    derivation: Optional[Dict[str, object]],
    seed_master: Optional[bytes],
    extra_fields: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    total_bytes = sum(int(a.get("size_bytes", 0) or 0) for a in artifact_entries)
    receipt: Dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "tool": "authored-pack",
        "tool_version": str(AUTHORED_PACK_VERSION),
        "pack_layout": PACK_LAYOUT_VERSION,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "pack_root_sha256": root_sha256,
        "payload_root_sha256": payload_root_sha256,
        "artifact_count": int(len(artifact_entries)),
        "artifact_bytes": int(total_bytes),
        "stamped_at_utc": _utc_now_iso(),
    }
    if pack_id:
        receipt["pack_id"] = str(pack_id)
    if zip_path is not None:
        # Avoid embedding absolute local paths in receipts.
        receipt["zip_path"] = str(Path(str(zip_path)).name)
    if derivation:
        receipt["derivation"] = dict(derivation)
    if seed_master is not None:
        fingerprint = seed_fingerprint_sha256(seed_master)
        receipt["derived_seed_fingerprint_sha256"] = fingerprint
    _merge_receipt_extra_fields(receipt, extra_fields)
    return receipt


def _print_seed_material(seed_master: bytes) -> None:
    seed_hex = seed_master.hex()
    seed_b64 = base64.b64encode(seed_master).decode("ascii")
    print("derived_seed.hex:", seed_hex)
    print("derived_seed.b64:", seed_b64)


def _write_zip_to_path(pack_dir: Path, zip_path: Path, *, receipt_override: Optional[str] = None) -> None:
    # Public zip is the finalized public projection of the pack: rooted metadata + payload only.
    include_relpaths = {"manifest.json", PACK_ROOT_ALIAS_FILENAME, "receipt.json"}
    exclude = {
        "seed_master.hex",
        "seed_master.b64",
        "authored_pack.zip",
        zip_path.name,
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_pack_archive_files(pack_dir, exclude_names=exclude, skip_nested_zips=False):
            rel = path.relative_to(pack_dir).as_posix()
            if rel.startswith("authored_sources/") or rel.startswith("authored_sources\\"):
                continue
            if rel in include_relpaths or rel.startswith("payload/"):
                zi = zipfile.ZipInfo(filename=rel)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = (0o644 & 0xFFFF) << 16
                with zf.open(zi, "w") as writer:
                    if rel == "receipt.json" and receipt_override is not None:
                        writer.write(receipt_override.encode("utf-8"))
                        continue
                    with trusted_binary_reader(path) as reader:
                        while True:
                            chunk = reader.read(1024 * 1024)
                            if not chunk:
                                break
                            writer.write(chunk)


def _write_zip(pack_dir: Path, zip_path: Path, *, receipt_override: Optional[str] = None) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=".ap-zip-", dir=str(zip_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        _write_zip_to_path(pack_dir, tmp_path, receipt_override=receipt_override)
        os.replace(tmp_path, zip_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _evidence_input_files(pack_dir: Path, *, output_names: Sequence[str]) -> List[Path]:
    exclude_names = {
        "seed_master.hex",
        "seed_master.b64",
        "authored_pack.zip",
    }
    exclude_names.update(str(name) for name in output_names)
    for candidate in pack_dir.iterdir():
        name = candidate.name
        if name.startswith(".ap-evidence-"):
            exclude_names.add(name)
        if name.startswith("authored_evidence_") and (name.endswith(".zip") or name.endswith(".zip.sha256")):
            exclude_names.add(name)
    return _iter_pack_archive_files(pack_dir, exclude_names=exclude_names, skip_nested_zips=False)


def _write_evidence_bundle_to_path(pack_dir: Path, zip_path: Path, *, root: str) -> None:
    include = _evidence_input_files(pack_dir, output_names=(zip_path.name,))

    fixed_dt = (1980, 1, 1, 0, 0, 0)
    entries: List[Dict[str, object]] = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src in include:
            rel = src.relative_to(pack_dir).as_posix()
            zi = zipfile.ZipInfo(filename=rel, date_time=fixed_dt)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o644 & 0xFFFF) << 16

            h = hashlib.sha256()
            size = 0
            with trusted_binary_reader(src) as r, zf.open(zi, "w") as w:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    h.update(chunk)
                    w.write(chunk)
            entries.append({"path": rel, "size_bytes": int(size), "sha256": h.hexdigest()})

        entries.sort(key=lambda d: str(d.get("path", "")))
        evidence_manifest: Dict[str, object] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "pack_root_sha256": str(root),
            "created_at_utc": _utc_now_iso(),
            "entries": entries,
        }
        payload = (
            json.dumps(evidence_manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
            + b"\n"
        )
        mzi = zipfile.ZipInfo(filename="evidence_manifest.json", date_time=fixed_dt)
        mzi.compress_type = zipfile.ZIP_DEFLATED
        mzi.external_attr = (0o644 & 0xFFFF) << 16
        zf.writestr(mzi, payload)

        mh = hashlib.sha256(payload).hexdigest()
        hzi = zipfile.ZipInfo(filename="evidence_manifest_sha256.txt", date_time=fixed_dt)
        hzi.compress_type = zipfile.ZIP_DEFLATED
        hzi.external_attr = (0o644 & 0xFFFF) << 16
        zf.writestr(hzi, (mh + "\n").encode("utf-8"))


def _verify_evidence_pair(pack_dir: Path, zip_path: Path, sidecar_path: Path) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    try:
        sidecar_raw = _read_file_bytes_limited(sidecar_path, max_bytes=128)
        sidecar_text = sidecar_raw.decode("utf-8")
        if len(sidecar_text) != 65 or not sidecar_text.endswith("\n") or not _is_sha256_hex(sidecar_text[:-1]):
            errors.append("evidence sidecar invalid")
            return False, errors
        actual_zip_sha, _ = trusted_sha256_hex(zip_path)
        if sidecar_text[:-1] != actual_zip_sha:
            errors.append("evidence sidecar hash mismatch")
            return False, errors

        root_raw = _read_file_bytes_limited(pack_dir / PACK_ROOT_ALIAS_FILENAME, max_bytes=256)
        root = root_raw.decode("utf-8").strip()
        with zipfile.ZipFile(zip_path, "r") as zf:
            infos = _zip_infos_limited(zf, max_zip_members=DEFAULT_MAX_ZIP_MEMBERS)
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                errors.append("evidence zip contains duplicate member names")
                return False, errors
            errors.extend(_validate_zip_infos(infos))
            if errors:
                return False, errors
            manifest_raw = _read_zip_member_bytes_limited(
                zf,
                "evidence_manifest.json",
                max_bytes=DEFAULT_MAX_MANIFEST_BYTES,
            )
            manifest_sha_raw = _read_zip_member_bytes_limited(
                zf,
                "evidence_manifest_sha256.txt",
                max_bytes=128,
            )
            if manifest_sha_raw != (hashlib.sha256(manifest_raw).hexdigest() + "\n").encode("utf-8"):
                errors.append("evidence manifest sidecar mismatch")
                return False, errors
            manifest_obj = _loads_strict_json_bytes(manifest_raw)
            if not isinstance(manifest_obj, dict):
                errors.append("evidence_manifest.json must be an object")
                return False, errors
            if manifest_obj.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
                errors.append("evidence manifest schema_version invalid")
            if manifest_obj.get("pack_root_sha256") != root:
                errors.append("evidence manifest pack_root_sha256 mismatch")
            if not _is_utc_timestamp(manifest_obj.get("created_at_utc")):
                errors.append("evidence manifest created_at_utc invalid")
            entries = manifest_obj.get("entries")
            if not isinstance(entries, list):
                errors.append("evidence manifest entries invalid")
                return False, errors

            expected_names = {"evidence_manifest.json", "evidence_manifest_sha256.txt"}
            validated_entries: List[Tuple[str, int, str]] = []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    errors.append(f"evidence entry[{index}] not an object")
                    continue
                path = entry.get("path")
                size = entry.get("size_bytes")
                sha = entry.get("sha256")
                if not isinstance(path, str) or not path or path != path.strip() or _contains_terminal_control(path):
                    errors.append(f"evidence entry[{index}].path invalid")
                    continue
                parts = path.split("/")
                if (
                    "\\" in path
                    or path.startswith("/")
                    or _looks_like_windows_drive(path)
                    or any(part == "" or part in (".", "..") for part in parts)
                    or PurePosixPath(path).as_posix() != path
                ):
                    errors.append(f"evidence entry[{index}].path invalid")
                    continue
                if path in expected_names:
                    errors.append(f"duplicate evidence entry path: {path}")
                    continue
                expected_names.add(path)
                if type(size) is not int or int(size) < 0:
                    errors.append(f"evidence entry[{index}].size_bytes invalid")
                    continue
                if not _is_sha256_hex(sha):
                    errors.append(f"evidence entry[{index}].sha256 invalid")
                    continue
                validated_entries.append((path, int(size), str(sha)))

            if errors:
                return False, errors
            current_names = {
                path.relative_to(pack_dir).as_posix()
                for path in _evidence_input_files(
                    pack_dir,
                    output_names=(zip_path.name, sidecar_path.name),
                )
            }
            manifest_names = {path for path, _size, _sha in validated_entries}
            if manifest_names != current_names:
                missing = sorted(current_names - manifest_names)
                extra = sorted(manifest_names - current_names)
                detail: List[str] = []
                if missing:
                    detail.append(f"missing={missing[:5]}")
                if extra:
                    detail.append(f"extra={extra[:5]}")
                errors.append("evidence manifest entry set stale relative to pack: " + " ".join(detail))
                return False, errors
            if set(names) != expected_names:
                errors.append("evidence zip member set mismatch")
                return False, errors
            for path, expected_size, expected_sha in validated_entries:
                raw = _read_zip_member_bytes_limited(zf, path, max_bytes=expected_size)
                if len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_sha:
                    errors.append(f"evidence entry bytes mismatch: {path}")
                    continue
                current_path = pack_dir / Path(*path.split("/"))
                _require_contained_path(pack_dir, current_path, label="evidence entry")
                current_sha, current_size = trusted_sha256_hex(current_path, max_bytes=expected_size)
                if current_size != expected_size or current_sha != expected_sha:
                    errors.append(f"evidence entry stale relative to pack: {path}")
    except Exception as exc:
        errors.append(f"invalid evidence pair: {exc}")
    return not errors, errors


def write_evidence_bundle(pack_dir: Path) -> Tuple[Path, Optional[str]]:
    """
    Write a tamper-*evident* evidence bundle zip into the pack directory.

    Notes:
    - This is not cryptographically "untamperable" without an external signature.
    - The bundle is still useful as an audit artifact: it contains exact bytes + a hash manifest.
    """
    # Name includes the pack root for human ergonomics.
    root = ""
    for name in (PACK_ROOT_ALIAS_FILENAME, LEGACY_ROOT_ALIAS_FILENAME):
        try:
            root = _read_file_bytes_limited(pack_dir / name, max_bytes=256).decode("utf-8").strip()
        except Exception:
            root = ""
        if root:
            break
    if not (isinstance(root, str) and len(root) == 64 and all(c in "0123456789abcdef" for c in root.lower())):
        # Fall back to pack dir name.
        root = pack_dir.name

    zip_name = f"authored_evidence_{root}.zip"
    zip_path = pack_dir / zip_name
    sidecar_path = pack_dir / f"{zip_name}.sha256"
    zip_fd, zip_tmp_name = tempfile.mkstemp(prefix=".ap-evidence-", suffix=".zip", dir=str(pack_dir))
    os.close(zip_fd)
    tmp_path = Path(zip_tmp_name)
    tmp_sidecar: Optional[Path] = None
    try:
        _write_evidence_bundle_to_path(pack_dir, tmp_path, root=root)
        zip_sha, _n = trusted_sha256_hex(tmp_path)
        sidecar_fd, sidecar_tmp_name = tempfile.mkstemp(
            prefix=".ap-evidence-sidecar-",
            suffix=".zip.sha256",
            dir=str(pack_dir),
        )
        os.close(sidecar_fd)
        tmp_sidecar = Path(sidecar_tmp_name)
        _safe_write_text(tmp_sidecar, zip_sha + "\n")
        pair_ok, pair_errors = _verify_evidence_pair(pack_dir, tmp_path, tmp_sidecar)
        if not pair_ok:
            raise ValueError("generated evidence pair failed verification: " + "; ".join(pair_errors))

        had_zip = zip_path.exists()
        had_sidecar = sidecar_path.exists()
        zip_backup: Optional[Path] = None
        sidecar_backup: Optional[Path] = None
        try:
            if had_zip:
                backup_fd, backup_name = tempfile.mkstemp(prefix=".ap-evidence-backup-", suffix=".zip", dir=str(pack_dir))
                os.close(backup_fd)
                zip_backup = Path(backup_name)
                os.replace(zip_path, zip_backup)
            if had_sidecar:
                backup_fd, backup_name = tempfile.mkstemp(
                    prefix=".ap-evidence-backup-",
                    suffix=".zip.sha256",
                    dir=str(pack_dir),
                )
                os.close(backup_fd)
                sidecar_backup = Path(backup_name)
                os.replace(sidecar_path, sidecar_backup)
            os.replace(tmp_path, zip_path)
            os.replace(tmp_sidecar, sidecar_path)
            published_ok, published_errors = _verify_evidence_pair(pack_dir, zip_path, sidecar_path)
            if not published_ok:
                raise ValueError("published evidence pair failed verification: " + "; ".join(published_errors))
        except Exception:
            try:
                zip_path.unlink()
            except OSError:
                pass
            try:
                sidecar_path.unlink()
            except OSError:
                pass
            if had_zip and zip_backup is not None and zip_backup.exists():
                os.replace(zip_backup, zip_path)
            if had_sidecar and sidecar_backup is not None and sidecar_backup.exists():
                os.replace(sidecar_backup, sidecar_path)
            raise
        finally:
            for backup in (zip_backup, sidecar_backup):
                if backup is None:
                    continue
                try:
                    backup.unlink()
                except OSError:
                    pass
    finally:
        for temporary in (tmp_path, tmp_sidecar):
            if temporary is None:
                continue
            try:
                temporary.unlink()
            except OSError:
                pass
    return zip_path, zip_sha


def _verify_pack_impl(
    pack_path: Path,
    *,
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS,
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
        max_zip_members = int(max_zip_members)
    except Exception:
        return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["invalid verify limits"])
    if max_manifest_bytes <= 0 or max_artifact_bytes <= 0 or max_total_bytes <= 0 or max_zip_members <= 0:
        return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["invalid verify limits"])

    if pack_path.is_dir():
        manifest_path = pack_path / "manifest.json"
        if not manifest_path.is_file():
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["missing manifest.json"])
        try:
            raw = _read_file_bytes_limited(manifest_path, max_bytes=max_manifest_bytes)
            manifest = _loads_strict_json_bytes(raw)
        except Exception as exc:
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"invalid manifest.json: {exc}"])
        if not isinstance(manifest, dict):
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["manifest.json must be an object"])
        artifact_entries, structure_errors = _validate_manifest_structure(manifest)
        if structure_errors:
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=structure_errors)
        schema_version = manifest.get("schema_version")
        try:
            root_sha = manifest_root_sha256(manifest)
        except Exception as exc:
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"invalid manifest.json: {exc}"])
        payload_root, payload_errors = _validate_manifest_payload_root(manifest, artifact_entries)
        errors.extend(payload_errors)

        if schema_version == MANIFEST_SCHEMA_VERSION:
            errors.extend(_validate_current_root_alias_in_dir(pack_path, root_sha=root_sha))
            receipt_path = pack_path / "receipt.json"
            if not receipt_path.is_file():
                errors.append("missing receipt.json")
            else:
                try:
                    raw_receipt = _read_file_bytes_limited(receipt_path, max_bytes=max_manifest_bytes)
                    receipt = _loads_strict_json_bytes(raw_receipt)
                    errors.extend(
                        _validate_current_receipt(
                            receipt,
                            manifest=manifest,
                            root_sha=root_sha,
                            artifact_entries=artifact_entries,
                        )
                    )
                except Exception as exc:
                    errors.append(f"invalid receipt.json: {exc}")
        else:
            errors.extend(_validate_legacy_root_alias_in_dir(pack_path, root_sha=root_sha))

        file_count, total_bytes, expected_payload_relpaths, artifact_errors = _verify_manifest_artifacts(
            artifact_entries,
            max_artifact_bytes=max_artifact_bytes,
            max_total_bytes=max_total_bytes,
            verify_one=lambda idx, rel_s, size, sha: _verify_one_artifact_in_dir(
                pack_path, idx=idx, rel_s=rel_s, size=size, sha=sha
            ),
        )
        errors.extend(artifact_errors)
        if "manifest.artifacts missing or empty" in artifact_errors:
            return VerificationResult(ok=False, root_sha256=root_sha, file_count=0, total_bytes=0, errors=errors)

        errors.extend(_check_payload_closure_in_dir(pack_path, expected=expected_payload_relpaths))

        return VerificationResult(
            ok=not errors,
            root_sha256=root_sha,
            file_count=file_count,
            total_bytes=total_bytes,
            errors=errors,
            payload_root_sha256=payload_root,
        )

    if pack_path.is_file() and pack_path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(pack_path, "r") as zf:
                try:
                    infos = _zip_infos_limited(zf, max_zip_members=max_zip_members)
                except ValueError as exc:
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[str(exc)])
                if reject_duplicate_zip_members:
                    names = [zi.filename for zi in infos]
                    if len(names) != len(set(names)):
                        return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["zip contains duplicate member names"])
                member_errors = _validate_zip_infos(infos)
                if member_errors:
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=member_errors)
                try:
                    raw = _read_zip_member_bytes_limited(zf, "manifest.json", max_bytes=max_manifest_bytes)
                except KeyError:
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["missing manifest.json in zip"])
                try:
                    manifest = _loads_strict_json_bytes(raw)
                except Exception as exc:
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"invalid manifest.json in zip: {exc}"])
                if not isinstance(manifest, dict):
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=["manifest.json must be an object"])
                artifact_entries, structure_errors = _validate_manifest_structure(manifest)
                if structure_errors:
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=structure_errors)
                schema_version = manifest.get("schema_version")

                try:
                    root_sha = manifest_root_sha256(manifest)
                except Exception as exc:
                    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"invalid manifest.json in zip: {exc}"])
                payload_root, payload_errors = _validate_manifest_payload_root(manifest, artifact_entries)
                errors.extend(payload_errors)
                if schema_version == MANIFEST_SCHEMA_VERSION:
                    errors.extend(_validate_current_root_alias_in_zip(zf, root_sha=root_sha))
                    try:
                        raw_receipt = _read_zip_member_bytes_limited(zf, "receipt.json", max_bytes=max_manifest_bytes)
                        receipt = _loads_strict_json_bytes(raw_receipt)
                        errors.extend(
                            _validate_current_receipt(
                                receipt,
                                manifest=manifest,
                                root_sha=root_sha,
                                artifact_entries=artifact_entries,
                                expect_zip_projection=True,
                            )
                        )
                    except KeyError:
                        errors.append("missing receipt.json in zip")
                    except Exception as exc:
                        errors.append(f"invalid receipt.json in zip: {exc}")
                else:
                    errors.extend(_validate_legacy_root_alias_in_zip(zf, root_sha=root_sha))

                file_count, total_bytes, expected_payload_relpaths, artifact_errors = _verify_manifest_artifacts(
                    artifact_entries,
                    max_artifact_bytes=max_artifact_bytes,
                    max_total_bytes=max_total_bytes,
                    verify_one=lambda idx, rel_s, size, sha: _verify_one_artifact_in_zip(
                        zf, idx=idx, rel_s=rel_s, size=size, sha=sha
                    ),
                )
                errors.extend(artifact_errors)
                if "manifest.artifacts missing or empty" in artifact_errors:
                    return VerificationResult(ok=False, root_sha256=root_sha, file_count=0, total_bytes=0, errors=errors)

                actual_payload_relpaths = _payload_relpaths_in_zip(zf)
                _append_unexpected_payload_errors(
                    errors,
                    expected=expected_payload_relpaths,
                    actual=actual_payload_relpaths,
                )
                _append_unexpected_zip_member_errors(
                    errors,
                    schema_version=schema_version,
                    actual=_non_payload_member_names_in_zip(zf),
                )

                return VerificationResult(
                    ok=not errors,
                    root_sha256=root_sha,
                    file_count=file_count,
                    total_bytes=total_bytes,
                    errors=errors,
                    payload_root_sha256=payload_root,
                )
        except zipfile.BadZipFile as exc:
            return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"invalid zip: {exc}"])

    return VerificationResult(ok=False, root_sha256="", file_count=0, total_bytes=0, errors=[f"unsupported pack path: {pack_path}"])


def verify_pack(
    pack_path: Path,
    *,
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS,
    reject_duplicate_zip_members: bool = True,
) -> VerificationResult:
    try:
        return _verify_pack_impl(
            pack_path,
            max_manifest_bytes=max_manifest_bytes,
            max_artifact_bytes=max_artifact_bytes,
            max_total_bytes=max_total_bytes,
            max_zip_members=max_zip_members,
            reject_duplicate_zip_members=reject_duplicate_zip_members,
        )
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        return VerificationResult(
            ok=False,
            root_sha256="",
            file_count=0,
            total_bytes=0,
            errors=[f"verification failed: {exc.__class__.__name__}: {message}"],
        )
