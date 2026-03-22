from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .safeio import hash_trusted_file


MANIFEST_SCHEMA_VERSION = "entropy.pack.v2"
PAYLOAD_IDENTITY_SCHEMA_VERSION = "entropy.payload.v1"
DEFAULT_DERIVATION_VERSION = "ENTROPYPACK-SEED-v1"


def stable_dumps(value: Any) -> str:
    # Strict JSON: disallow NaN/Infinity to keep the root stable and portable.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256_hex(path: Path) -> str:
    digest, _size = hash_trusted_file(path)
    return digest


def is_hidden_path(path: Path) -> bool:
    parts = path.parts
    return any(p.startswith(".") and p not in (".", "..") for p in parts)


def _iter_files(root: Path, *, include_hidden: bool) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        # Deterministic traversal: sort in-place.
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            p = base / name
            rel = p.relative_to(root)
            if not include_hidden and is_hidden_path(rel):
                continue
            yield p


def collect_artifacts(
    input_dir: Path,
    *,
    include_hidden: bool = False,
    exclude_relpaths: Optional[Sequence[str]] = None,
) -> List[Dict[str, object]]:
    input_dir = input_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"input_dir must be a directory: {input_dir}")

    exclude: Optional[set[str]] = None
    if exclude_relpaths:
        exclude = {str(x) for x in exclude_relpaths if str(x).strip()}

    artifacts: List[Dict[str, object]] = []
    for path in _iter_files(input_dir, include_hidden=include_hidden):
        rel = path.relative_to(input_dir).as_posix()
        if exclude is not None and rel in exclude:
            continue
        digest, size = hash_trusted_file(path)
        artifacts.append(
            {
                "source_relpath": rel,
                "sha256": digest,
                "size_bytes": int(size),
            }
        )
    return artifacts


def normalize_dice(dice: Sequence[Tuple[str, int]]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for die, value in dice:
        die_s = str(die).strip()
        if not die_s:
            raise ValueError("dice die name cannot be empty")
        if not isinstance(value, int):
            raise ValueError("dice value must be int")
        out.append({"die": die_s, "value": int(value)})
    out.sort(key=lambda d: (str(d.get("die", "")), int(d.get("value", 0))))
    return out


def build_manifest(
    *,
    pack_id: Optional[str],
    artifacts: Sequence[Dict[str, object]],
    payload_root_sha256: Optional[str] = None,
    notes: Optional[str] = None,
    created_at_utc: Optional[str] = None,
    dice: Optional[Sequence[Tuple[str, int]]] = None,
    derivation: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    manifest: Dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifacts": list(artifacts),
    }
    if payload_root_sha256:
        manifest["payload_root_sha256"] = str(payload_root_sha256)
    if pack_id:
        manifest["pack_id"] = str(pack_id)
    if notes:
        manifest["notes"] = str(notes)
    if created_at_utc:
        manifest["created_at_utc"] = str(created_at_utc)
    if dice:
        manifest["dice"] = normalize_dice(dice)
    if derivation:
        manifest["derivation"] = dict(derivation)
    return manifest


def manifest_root_sha256(manifest: Dict[str, object]) -> str:
    canonical = stable_dumps(manifest).encode("utf-8")
    return sha256_hex(canonical)


def payload_root_sha256(artifacts: Sequence[Dict[str, object]]) -> str:
    normalized: List[Dict[str, object]] = []
    for a in artifacts:
        normalized.append(
            {
                "path": str(a.get("path", "")),
                "sha256": str(a.get("sha256", "")),
                "size_bytes": int(a.get("size_bytes", 0) or 0),
            }
        )
    normalized.sort(key=lambda d: str(d.get("path", "")))
    canonical = stable_dumps(
        {
            "schema_version": PAYLOAD_IDENTITY_SCHEMA_VERSION,
            "artifacts": normalized,
        }
    ).encode("utf-8")
    return sha256_hex(canonical)


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    root_sha256: str
    file_count: int
    total_bytes: int
    errors: List[str]
    payload_root_sha256: str = ""
