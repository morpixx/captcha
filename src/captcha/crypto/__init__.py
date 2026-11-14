"""Crypto primitives for antibot payloads."""

from __future__ import annotations

from .aes import (
    CryptoError,
    aes_cbc_decrypt,
    aes_cbc_encrypt,
    aes_ctr_xor,
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    pkcs7_pad,
    pkcs7_unpad,
)
from .compress import (
    gzip_compress,
    gzip_decompress,
    raw_deflate,
    raw_inflate,
    zlib_deflate,
    zlib_inflate,
)
from .hmac import hmac_sha1, hmac_sha256, hmac_sha384, hmac_sha512, hmac_verify
from .rsa import (
    load_public_key,
    load_public_key_from_modexp,
    rsa_public_encrypt_oaep,
    rsa_public_encrypt_pkcs1v15,
)
from .xor import xor_cipher, xor_evolving_key, xor_with_counter

__all__ = [
    "CryptoError",
    "aes_cbc_decrypt",
    "aes_cbc_encrypt",
    "aes_ctr_xor",
    "aes_ecb_decrypt",
    "aes_ecb_encrypt",
    "aes_gcm_decrypt",
    "aes_gcm_encrypt",
    "gzip_compress",
    "gzip_decompress",
    "hmac_sha1",
    "hmac_sha256",
    "hmac_sha384",
    "hmac_sha512",
    "hmac_verify",
    "load_public_key",
    "load_public_key_from_modexp",
    "pkcs7_pad",
    "pkcs7_unpad",
    "raw_deflate",
    "raw_inflate",
    "rsa_public_encrypt_oaep",
    "rsa_public_encrypt_pkcs1v15",
    "xor_cipher",
    "xor_evolving_key",
    "xor_with_counter",
    "zlib_deflate",
    "zlib_inflate",
]
