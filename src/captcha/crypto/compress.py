"""Compression helpers. Raw deflate (no zlib header) and gzip wrapper.

Sensor payloads often ship raw DEFLATE bytes with no zlib framing because the
JS side used `pako.deflateRaw`. `raw_deflate`/`raw_inflate` match that shape.
"""

from __future__ import annotations

import gzip
import zlib

from .aes import CryptoError


def raw_deflate(data: bytes, *, level: int = 6) -> bytes:
    """DEFLATE without zlib header/checksum. Matches pako.deflateRaw."""
    co = zlib.compressobj(level, zlib.DEFLATED, -zlib.MAX_WBITS)
    return co.compress(data) + co.flush()


def raw_inflate(data: bytes, *, max_size: int = 64 * 1024 * 1024) -> bytes:
    """Inverse of raw_deflate. Guards against zip-bombs via max_size."""
    try:
        d = zlib.decompressobj(-zlib.MAX_WBITS)
        out = d.decompress(data, max_size)
        if d.unconsumed_tail:
            raise CryptoError(f"inflate exceeded max_size={max_size}")
        return out + d.flush()
    except zlib.error as e:
        raise CryptoError(f"raw inflate failed: {e}") from e


def zlib_deflate(data: bytes, *, level: int = 6) -> bytes:
    """DEFLATE with zlib header + adler32. Matches pako.deflate."""
    return zlib.compress(data, level)


def zlib_inflate(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except zlib.error as e:
        raise CryptoError(f"zlib inflate failed: {e}") from e


def gzip_compress(data: bytes, *, level: int = 6) -> bytes:
    return gzip.compress(data, compresslevel=level)


def gzip_decompress(data: bytes) -> bytes:
    try:
        return gzip.decompress(data)
    except (OSError, zlib.error) as e:
        raise CryptoError(f"gzip decompress failed: {e}") from e
