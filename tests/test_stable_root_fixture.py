from __future__ import annotations

import json
from pathlib import Path

from authored_pack.manifest import manifest_root_sha256, payload_root_sha256, stable_dumps
from authored_pack.pack import verify_pack


FIXTURE = Path(__file__).parent / "fixtures" / "stable_root_v1"
EXPECTED_PAYLOAD_ROOT = "daa940f96d77819b1ed229556eea022b5749231e134c5cd51a4c6b9af74d1650"
EXPECTED_PACK_ROOT = "e71cf6899448f275a6eb6f1ef55a50ba2d4a01fb2088fec41d35b90742d2c031"
EXPECTED_MANIFEST_BYTES = (
    b'{"artifacts":[{"path":"payload/note.txt","sha256":"f57d768d8bc93470ad9a46fbbbd5ae8076c1fa65c4fc3994fc45fdac832c99a7",'
    b'"size_bytes":15}],"created_at_utc":"2026-07-20T00:00:00Z","pack_id":"stable-root-v1",'
    b'"payload_root_sha256":"daa940f96d77819b1ed229556eea022b5749231e134c5cd51a4c6b9af74d1650",'
    b'"schema_version":"authored.pack.v1"}'
)


def test_stable_root_v1_fixture_locks_producer_and_verifier_contract() -> None:
    manifest_file_bytes = (FIXTURE / "manifest.json").read_bytes()
    assert manifest_file_bytes == EXPECTED_MANIFEST_BYTES + b"\n"
    manifest = json.loads(manifest_file_bytes)
    assert stable_dumps(manifest).encode("utf-8") == EXPECTED_MANIFEST_BYTES
    assert payload_root_sha256(manifest["artifacts"]) == EXPECTED_PAYLOAD_ROOT
    assert manifest_root_sha256(manifest) == EXPECTED_PACK_ROOT

    result = verify_pack(FIXTURE)
    assert result.ok, result.errors
    assert result.payload_root_sha256 == EXPECTED_PAYLOAD_ROOT
    assert result.root_sha256 == EXPECTED_PACK_ROOT
