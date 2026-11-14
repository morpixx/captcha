"""Fingerprint store. File-backed JSON today, S3 FILL below."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterable
from pathlib import Path

import msgspec
import orjson

from captcha.adapters.profiles.bundles import DEFAULT_BUNDLES
from captcha.domain.errors import ConfigError
from captcha.domain.profile import Profile


class Store:
    """Fingerprint repository.

    `path_or_s3` is either a filesystem directory (one `*.json` per profile,
    or a single `profiles.json` list), or an `s3://bucket/prefix` URI.

    FILL: implement S3 backend. Dispatch on the scheme at `load_all`.
    """

    def __init__(self, path_or_s3: str | Path, *, seed_defaults: bool = True) -> None:
        self._source = str(path_or_s3)
        self._profiles: dict[str, Profile] = {}
        self._seed_defaults = seed_defaults

    async def load_all(self) -> int:
        """Load every profile the source exposes. Returns count."""
        if self._source.startswith("s3://"):
            # FILL: wire aioboto3 — list objects under prefix, stream each key,
            # msgspec.json.decode each body into Profile. Keep concurrency bounded.
            raise ConfigError(
                "S3 backend not yet wired — see FILL in captcha.adapters.profiles.store",
                context={"source": self._source},
            )

        self._profiles.clear()
        if self._seed_defaults:
            self._profiles.update(DEFAULT_BUNDLES)

        p = Path(self._source)
        if p.exists():
            await asyncio.to_thread(self._load_from_disk, p)

        return len(self._profiles)

    def _load_from_disk(self, root: Path) -> None:
        if root.is_file():
            self._ingest_file(root)
            return
        for child in root.glob("*.json"):
            self._ingest_file(child)

    def _ingest_file(self, path: Path) -> None:
        data = orjson.loads(path.read_bytes())
        if isinstance(data, list):
            for raw in data:
                prof = msgspec.convert(raw, Profile)
                self._profiles[prof.id] = prof
        elif isinstance(data, dict):
            prof = msgspec.convert(data, Profile)
            self._profiles[prof.id] = prof
        else:
            raise ConfigError(f"unexpected fingerprint file layout at {path}")

    def all(self) -> list[Profile]:
        return list(self._profiles.values())

    def by_id(self, profile_id: str) -> Profile | None:
        return self._profiles.get(profile_id)

    def pick(self, filter: dict[str, str] | None = None, *, rng: random.Random | None = None) -> Profile:
        """Random pick with a `tags`-dict filter. All filter keys must match."""
        pool = list(_filter(self._profiles.values(), filter or {}))
        if not pool:
            raise ConfigError(
                "no fingerprint matches filter",
                context={"filter": filter or {}, "have": len(self._profiles)},
            )
        return (rng or random).choice(pool)


def pick_profile(store: Store, filter: dict[str, str] | None = None) -> Profile:
    """Module-level convenience — mirrors Store.pick."""
    return store.pick(filter)


def _filter(profiles: Iterable[Profile], f: dict[str, str]) -> Iterable[Profile]:
    if not f:
        return profiles
    out: list[Profile] = []
    for p in profiles:
        if all(p.tags.get(k) == v for k, v in f.items()):
            out.append(p)
    return out
