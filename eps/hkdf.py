from __future__ import annotations

import hashlib
import hmac


def hkdf_sha256(*, ikm: bytes, length: int, salt: bytes = b"", info: bytes = b"") -> bytes:
    """
    RFC 5869 HKDF (SHA-256), stdlib-only.

    Args:
        ikm: input key material
        length: output length in bytes
        salt: optional salt (may be empty)
        info: optional context string
    """
    if length <= 0:
        raise ValueError("length must be > 0")
    if not isinstance(ikm, (bytes, bytearray)):
        raise TypeError("ikm must be bytes")
    if not isinstance(salt, (bytes, bytearray)):
        raise TypeError("salt must be bytes")
    if not isinstance(info, (bytes, bytearray)):
        raise TypeError("info must be bytes")

    hash_len = hashlib.sha256().digest_size
    if length > 255 * hash_len:
        raise ValueError("length too large for HKDF-SHA256")

    # Extract
    prk = hmac.new(bytes(salt), bytes(ikm), hashlib.sha256).digest()

    # Expand
    okm = b""
    t = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + bytes(info) + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]
