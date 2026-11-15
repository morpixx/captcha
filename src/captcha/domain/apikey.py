"""API-key hashing at rest.

An account key is stored as HMAC-SHA256(pepper, key); the plaintext key exists
only in the client's hands and in the one process that minted it. `key_tail`
keeps the last four characters so an operator or the cabinet can tell two keys
apart without being able to use them.

Pure stdlib so this can live in domain and be imported by the storage adapter,
the CLI and the migration alike.
"""

from __future__ import annotations

import hmac
from hashlib import sha256

#: Characters kept in `accounts.key_tail`.
KEY_TAIL_LEN = 4


def hash_api_key(api_key: str, pepper: str = "") -> str:
    """Hex HMAC-SHA256 of the key. The pepper is process-wide config; empty is
    allowed but removes the rainbow-table protection, so production sets it."""
    return hmac.new(pepper.encode("utf-8"), api_key.encode("utf-8"), sha256).hexdigest()


def key_tail(api_key: str, length: int = KEY_TAIL_LEN) -> str:
    """Last `length` characters of the key, for display only."""
    if not api_key:
        return ""
    return api_key[-length:]


__all__ = ["KEY_TAIL_LEN", "hash_api_key", "key_tail"]
