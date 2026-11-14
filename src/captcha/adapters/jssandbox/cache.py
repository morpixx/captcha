"""Hash-keyed script cache. Keeps compiled source text keyed so the same CF interrogator
blob isn't re-parsed per request."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any


def script_hash(source: str) -> str:
    return hashlib.blake2b(source.encode("utf-8"), digest_size=16).hexdigest()


class ScriptCache:
    """Bounded LRU keyed by blake2b(source).

    Stores arbitrary per-script artifacts — for `LocalRuntime` it's the source itself
    pre-wrapped with context bootstrap; for a sidecar it's compiled bytecode handles
    the sidecar returned. The cache is runtime-agnostic.
    """

    def __init__(self, capacity: int = 256) -> None:
        self._capacity = capacity
        self._store: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            val = self._store.get(key)
            if val is not None:
                self._store.move_to_end(key)
            return val

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value
            self._store.move_to_end(key)
            while len(self._store) > self._capacity:
                self._store.popitem(last=False)

    def get_or_compute(self, source: str, factory) -> tuple[str, Any]:
        key = script_hash(source)
        cached = self.get(key)
        if cached is not None:
            return key, cached
        value = factory(source)
        self.put(key, value)
        return key, value

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
