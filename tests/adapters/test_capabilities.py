"""Lane heartbeat + capability sets.

In-memory Redis stand-in: the contract is about TTLs and stale sets, not about
the client library. No real Redis is touched.
"""

from __future__ import annotations

import fnmatch

from captcha.adapters.queue.capabilities import (
    HEARTBEAT_TTL_SECONDS,
    CapabilityRegistry,
    capability_key,
    gateway_accepts,
    gateway_live_types,
    gateway_ready,
    heartbeat_key,
)
from captcha.domain.enums import CaptchaType


class FakeRedis:
    """In-memory subset of redis.asyncio used by capabilities."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.strings[key] = value
        self.ttls[key] = ttl

    async def sadd(self, key: str, *members: str) -> None:
        self.sets.setdefault(key, set()).update(members)

    async def smembers(self, key: str) -> set[bytes]:
        # redis.asyncio with decode_responses=False returns bytes.
        return {m.encode() for m in self.sets.get(key, set())}

    async def exists(self, key: str) -> int:
        return int(key in self.strings)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self.strings.pop(key, None) is not None)
            removed += int(self.sets.pop(key, None) is not None)
        return removed

    def scan_iter(self, match: str | None = None, count: int | None = None):
        async def _iter():
            for key in list(self.strings):
                if match is None or fnmatch.fnmatch(key, match):
                    yield key.encode()

        return _iter()


def _caps(redis: FakeRedis) -> CapabilityRegistry:
    return CapabilityRegistry(redis)  # type: ignore[arg-type]


async def test_heartbeat_sets_ttl_and_declares_types() -> None:
    redis = FakeRedis()

    await _caps(redis).heartbeat("browser", "w-1", [CaptchaType.HCAPTCHA, CaptchaType.TURNSTILE])

    key = heartbeat_key("captcha", "browser", "w-1")
    assert redis.strings[key] == "1"
    assert redis.ttls[key] == HEARTBEAT_TTL_SECONDS == 90
    assert redis.sets[capability_key("captcha", "browser")] == {"hcaptcha", "turnstile"}


async def test_worker_alive_reads_its_own_key() -> None:
    redis = FakeRedis()
    caps = _caps(redis)

    assert await caps.worker_alive("browser", "w-1") is False
    await caps.heartbeat("browser", "w-1", [CaptchaType.HCAPTCHA])
    assert await caps.worker_alive("browser", "w-1") is True
    assert await caps.worker_alive("http", "w-1") is False


async def test_expired_heartbeat_hides_a_stale_capability_set() -> None:
    redis = FakeRedis()
    caps = _caps(redis)
    await caps.heartbeat("http", "w-1", [CaptchaType.RECAPTCHA_V3])

    # Simulate SETEX expiry: the key is gone, the cap set is not.
    redis.strings.clear()

    assert await caps.live_lanes() == ()
    assert await caps.live_types() == frozenset()
    assert await gateway_accepts(redis, CaptchaType.RECAPTCHA_V3) is False  # type: ignore[arg-type]
    assert await gateway_ready(redis) is False  # type: ignore[arg-type]


async def test_gateway_helpers_union_only_live_lanes() -> None:
    redis = FakeRedis()
    caps = _caps(redis)
    await caps.heartbeat("http", "w-1", [CaptchaType.RECAPTCHA_V3])
    await caps.heartbeat("browser", "w-2", [CaptchaType.HCAPTCHA])

    live = await gateway_live_types(redis)  # type: ignore[arg-type]
    assert live == frozenset({CaptchaType.RECAPTCHA_V3, CaptchaType.HCAPTCHA})
    assert await gateway_accepts(redis, CaptchaType.HCAPTCHA) is True  # type: ignore[arg-type]
    assert await gateway_accepts(redis, CaptchaType.SLIDER) is False  # type: ignore[arg-type]
    assert await gateway_ready(redis) is True  # type: ignore[arg-type]


async def test_types_for_lane_ignores_unknown_members() -> None:
    redis = FakeRedis()
    caps = _caps(redis)
    await caps.heartbeat("browser", "w-1", [CaptchaType.HCAPTCHA])
    redis.sets[capability_key("captcha", "browser")].add("not_a_real_type")

    assert await caps.types_for_lane("browser") == frozenset({CaptchaType.HCAPTCHA})


async def test_clear_drops_capability_set_with_the_last_worker() -> None:
    redis = FakeRedis()
    caps = _caps(redis)
    await caps.heartbeat("browser", "w-1", [CaptchaType.HCAPTCHA])
    await caps.heartbeat("browser", "w-2", [CaptchaType.HCAPTCHA])

    await caps.clear("browser", "w-1")
    assert await caps.worker_alive("browser", "w-1") is False
    assert capability_key("captcha", "browser") in redis.sets, "w-2 is still alive"
    assert await caps.live_types() == frozenset({CaptchaType.HCAPTCHA})

    await caps.clear("browser", "w-2")
    assert capability_key("captcha", "browser") not in redis.sets
    assert await caps.live_types() == frozenset()
