"""HMAC helpers. Thin wrappers that return raw bytes; hex on the caller side."""

from __future__ import annotations

import hmac as _hmac
from hashlib import md5, sha1, sha256, sha384, sha512


def hmac_md5(key: bytes, data: bytes) -> bytes:
    """HMAC-MD5. PerimeterX's `pc` field is the only user; MD5 is theirs, not ours."""
    return _hmac.new(key, data, md5).digest()


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    return _hmac.new(key, data, sha256).digest()


def hmac_sha1(key: bytes, data: bytes) -> bytes:
    return _hmac.new(key, data, sha1).digest()


def hmac_sha384(key: bytes, data: bytes) -> bytes:
    return _hmac.new(key, data, sha384).digest()


def hmac_sha512(key: bytes, data: bytes) -> bytes:
    return _hmac.new(key, data, sha512).digest()


def hmac_verify(key: bytes, data: bytes, mac: bytes, *, algo: str = "sha256") -> bool:
    """Constant-time compare. `algo` is md5|sha1|sha256|sha384|sha512."""
    table = {
        "md5": hmac_md5,
        "sha1": hmac_sha1,
        "sha256": hmac_sha256,
        "sha384": hmac_sha384,
        "sha512": hmac_sha512,
    }
    fn = table[algo.lower()]
    return _hmac.compare_digest(fn(key, data), mac)
