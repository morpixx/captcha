"""Arkose BDA transport envelope.

The token endpoint takes the BDA as one opaque field:

    b64(iv:12) || b64(tag:16) || b64(rsaBlock) || b64(ciphertext)

A random AES-256-GCM key encrypts the BDA JSON; the key is wrapped with
RSA-OAEP(SHA-256) under the site's SPKI public key. Recovered from the
deobfuscated api.js (public RE, 2026-07), verified there byte-for-byte against
live capi 4.4.x mints — not against ours yet, so a wrong key shows up as a
rejected BDA (SensorError), never as a silent success.
"""

from __future__ import annotations

import base64
import os

from captcha.domain.errors import SensorError

GCM_TAG_BYTES = 16
AES_KEY_BYTES = 32
GCM_IV_BYTES = 12


def encrypt_bda(plaintext: str, rsa_public_key_b64: str) -> str:
    """Wrap a marshalled BDA into the field the token endpoint expects."""
    public_key = load_spki(rsa_public_key_b64)
    key = os.urandom(AES_KEY_BYTES)
    iv = os.urandom(GCM_IV_BYTES)

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        sealed = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)
        wrapped = public_key.encrypt(
            key,
            _oaep(),
        )
    except Exception as exc:  # pragma: no cover - crypto backend failure
        raise SensorError("arkose envelope encrypt failed", context={"error": str(exc)}) from exc

    ciphertext, tag = sealed[:-GCM_TAG_BYTES], sealed[-GCM_TAG_BYTES:]
    encode = lambda blob: base64.b64encode(blob).decode("ascii")  # noqa: E731
    return encode(iv) + encode(tag) + encode(wrapped) + encode(ciphertext)


def load_spki(rsa_public_key_b64: str):
    """Parse a base64 SPKI (the blob api.js carries) into a public key object."""
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    raw = (rsa_public_key_b64 or "").strip()
    if not raw:
        raise SensorError("arkose envelope needs the site RSA public key")
    try:
        return load_der_public_key(base64.b64decode(raw))
    except Exception as exc:
        raise SensorError(
            "arkose RSA public key is not a base64 SPKI",
            context={"prefix": raw[:16], "length": len(raw), "error": str(exc)},
        ) from exc


def _oaep():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
