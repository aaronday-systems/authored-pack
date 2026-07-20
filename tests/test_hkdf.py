from __future__ import annotations

import unittest

from authored_pack.hkdf import hkdf_sha256
from authored_pack.pack import derive_seed_master


class TestHkdf(unittest.TestCase):
    def test_hkdf_sha256_rfc5869_case_1(self) -> None:
        # RFC 5869 Appendix A.1 (SHA-256)
        ikm = bytes.fromhex("0b" * 22)
        salt = bytes.fromhex("000102030405060708090a0b0c")
        info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
        okm = hkdf_sha256(ikm=ikm, length=42, salt=salt, info=info)
        expected = bytes.fromhex(
            "3cb25f25faacd57a90434f64d0362f2a"
            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"
        )
        self.assertEqual(okm, expected)

    def test_derive_seed_master_rejects_invalid_sources_hex(self) -> None:
        with self.assertRaises(ValueError):
            derive_seed_master(
                root_sha256_hex="aa" * 32,
                authored_sources_sha256_hex="not-valid-hex",
            )

    def test_derive_seed_master_rejects_wrong_sources_length(self) -> None:
        # valid hex, but decodes to 31 bytes instead of 32.
        with self.assertRaises(ValueError):
            derive_seed_master(
                root_sha256_hex="aa" * 32,
                authored_sources_sha256_hex="11" * 31,
            )

    def test_derive_seed_master_with_valid_sources_is_deterministic(self) -> None:
        root = "aa" * 32
        sources = "11" * 32
        a = derive_seed_master(root_sha256_hex=root, authored_sources_sha256_hex=sources)
        b = derive_seed_master(root_sha256_hex=root, authored_sources_sha256_hex=sources)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 32)

    def test_hkdf_rejects_non_bytes_salt(self) -> None:
        with self.assertRaises(TypeError):
            hkdf_sha256(ikm=b"ikm", length=32, salt="salt")  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            hkdf_sha256(ikm=b"ikm", length=32, salt="salt", info=b"info")  # type: ignore[arg-type]

    def test_hkdf_rejects_non_bytes_info(self) -> None:
        with self.assertRaises(TypeError):
            hkdf_sha256(ikm=b"ikm", length=32, info="info")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            hkdf_sha256(ikm=b"ikm", length=32, salt=b"salt", info="info")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
