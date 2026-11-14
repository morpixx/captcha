"""AES primitives. CBC with PKCS7, GCM with AAD, CTR with explicit counter.

All functions take raw bytes. Keys must be 16/24/32 bytes. IVs are caller-supplied;
nothing here generates randomness silently.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from captcha.domain.errors import CaptchaError


class CryptoError(CaptchaError):
    code = "ERR_CRYPTO"


def _check_key(key: bytes) -> None:
    if len(key) not in (16, 24, 32):
        raise CryptoError(f"AES key must be 16/24/32 bytes, got {len(key)}")


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    padder = padding.PKCS7(block_size * 8).padder()
    return padder.update(data) + padder.finalize()


def pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    unpadder = padding.PKCS7(block_size * 8).unpadder()
    try:
        return unpadder.update(data) + unpadder.finalize()
    except ValueError as e:
        raise CryptoError(f"pkcs7 unpad failed: {e}") from e


# ---- CBC --------------------------------------------------------------


def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    _check_key(key)
    if len(iv) != 16:
        raise CryptoError(f"AES-CBC IV must be 16 bytes, got {len(iv)}")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(pkcs7_pad(plaintext)) + encryptor.finalize()


def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    _check_key(key)
    if len(iv) != 16:
        raise CryptoError(f"AES-CBC IV must be 16 bytes, got {len(iv)}")
    if len(ciphertext) % 16:
        raise CryptoError(f"AES-CBC ciphertext not block-aligned: {len(ciphertext)}")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return pkcs7_unpad(padded)


# ---- GCM --------------------------------------------------------------


def aes_gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes, *, aad: bytes = b"") -> bytes:
    """Return nonce-less ciphertext || tag(16). Caller tracks the nonce."""
    _check_key(key)
    if len(nonce) != 12:
        raise CryptoError(f"AES-GCM nonce must be 12 bytes, got {len(nonce)}")
    return AESGCM(key).encrypt(nonce, plaintext, aad or None)


def aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext_and_tag: bytes, *, aad: bytes = b"") -> bytes:
    _check_key(key)
    if len(nonce) != 12:
        raise CryptoError(f"AES-GCM nonce must be 12 bytes, got {len(nonce)}")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext_and_tag, aad or None)
    except Exception as e:
        raise CryptoError(f"AES-GCM auth failed: {e}") from e


# ---- CTR (explicit counter) -------------------------------------------


def aes_ctr_xor(key: bytes, nonce_counter: bytes, data: bytes) -> bytes:
    """AES-CTR stream. `nonce_counter` is the full 16-byte initial block.

    Some antibots pack nonce+counter in weird layouts (e.g. 8 bytes nonce + 8
    bytes BE counter starting at 1). Build the initial block in the caller.
    """
    _check_key(key)
    if len(nonce_counter) != 16:
        raise CryptoError(f"AES-CTR initial block must be 16 bytes, got {len(nonce_counter)}")
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce_counter))
    enc = cipher.encryptor()
    return enc.update(data) + enc.finalize()


# ---- ECB (rare, only for single-block RE work) ------------------------


def aes_ecb_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """ECB with PKCS7. Only exists because RE sometimes needs it."""
    _check_key(key)
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(pkcs7_pad(plaintext)) + enc.finalize()


def aes_ecb_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    _check_key(key)
    if len(ciphertext) % 16:
        raise CryptoError(f"AES-ECB ciphertext not block-aligned: {len(ciphertext)}")
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    dec = cipher.decryptor()
    return pkcs7_unpad(dec.update(ciphertext) + dec.finalize())
