"""RSA public-encrypt helpers. Private-key ops not exposed on purpose."""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as apadding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key, load_pem_public_key

from captcha.domain.errors import CaptchaError

from .aes import CryptoError


def load_public_key(key: bytes | str) -> RSAPublicKey:
    """Accept PEM or DER bytes, or a PEM string. Returns an RSAPublicKey."""
    if isinstance(key, str):
        key = key.encode()
    try:
        if key.lstrip().startswith(b"-----BEGIN"):
            pk = load_pem_public_key(key)
        else:
            pk = load_der_public_key(key)
    except Exception as e:
        raise CryptoError(f"rsa load failed: {e}") from e
    if not isinstance(pk, RSAPublicKey):
        raise CryptoError(f"not an RSA public key: {type(pk).__name__}")
    return pk


def load_public_key_from_modexp(n: int | bytes | str, e: int | bytes | str = 65537) -> RSAPublicKey:
    """Build an RSA public key from (n, e). Accepts int, bytes (big-endian), or hex string."""
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    def _to_int(v: int | bytes | str) -> int:
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            s = v.strip().removeprefix("0x")
            return int(s, 16)
        return int.from_bytes(v, "big")

    try:
        return RSAPublicNumbers(e=_to_int(e), n=_to_int(n)).public_key()
    except Exception as exc:
        raise CryptoError(f"rsa (n,e) build failed: {exc}") from exc


def rsa_public_encrypt_oaep(
    public_key: RSAPublicKey | bytes | str,
    plaintext: bytes,
    *,
    hash_algo: str = "sha256",
    mgf_hash: str | None = None,
    label: bytes | None = None,
) -> bytes:
    pk = public_key if isinstance(public_key, RSAPublicKey) else load_public_key(public_key)
    h = _hash(hash_algo)
    mgf = _hash(mgf_hash) if mgf_hash else h
    try:
        return pk.encrypt(
            plaintext,
            apadding.OAEP(mgf=apadding.MGF1(mgf), algorithm=h, label=label),
        )
    except Exception as e:
        raise CryptoError(f"rsa-oaep encrypt failed: {e}") from e


def rsa_public_encrypt_pkcs1v15(
    public_key: RSAPublicKey | bytes | str,
    plaintext: bytes,
) -> bytes:
    pk = public_key if isinstance(public_key, RSAPublicKey) else load_public_key(public_key)
    try:
        return pk.encrypt(plaintext, apadding.PKCS1v15())
    except Exception as e:
        raise CryptoError(f"rsa-pkcs1v15 encrypt failed: {e}") from e


def _hash(name: str) -> hashes.HashAlgorithm:
    n = name.lower().replace("-", "")
    table = {
        "sha1": hashes.SHA1,
        "sha224": hashes.SHA224,
        "sha256": hashes.SHA256,
        "sha384": hashes.SHA384,
        "sha512": hashes.SHA512,
    }
    if n not in table:
        raise CaptchaError(f"unsupported hash: {name}")
    return table[n]()
