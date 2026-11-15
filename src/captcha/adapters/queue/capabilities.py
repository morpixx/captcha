"""Worker liveness and per-lane type capabilities.

The gateway may only sell a type when a worker for its lane is alive *right
now*. Liveness is one short-TTL Redis key per worker; the solvable types are a
plain set per lane, so the gateway does not have to interrogate every worker.

Keys (contract §2):

    SETEX captcha:lane:<lane>:<worker> 90 1
    SADD  captcha:cap:<lane> <type>...

`setex` guarantees a crashed worker's heartbeat disappears within the TTL.
The capability set deliberately outlives it: a lane with no live heartbeat is
ignored as a whole, so a stale set can never resurrect a dead lane.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Final

from captcha.domain.catalog import LANES
from captcha.domain.enums import CaptchaType
from captcha.domain.lanes import Lane

if TYPE_CHECKING:
    from redis.asyncio import Redis

#: Contract §2. Refreshed every ~30s, so one lost beat is not an outage.
HEARTBEAT_TTL_SECONDS: Final[int] = 90
DEFAULT_PREFIX: Final[str] = "captcha"


def heartbeat_key(prefix: str, lane: Lane, worker: str) -> str:
    return f"{prefix}:lane:{lane}:{worker}"


def capability_key(prefix: str, lane: Lane) -> str:
    return f"{prefix}:cap:{lane}"


def _type_value(t: CaptchaType | str) -> str:
    return t.value if isinstance(t, CaptchaType) else t


def _parse_types(raw: Iterable[bytes | str]) -> frozenset[CaptchaType]:
    out: set[CaptchaType] = set()
    for item in raw:
        value = item.decode("utf-8", "replace") if isinstance(item, (bytes, bytearray)) else str(item)
        try:
            out.add(CaptchaType(value))
        except ValueError:
            # A newer worker advertises a type this deployment does not know.
            # Ignore it instead of breaking the whole capability read.
            continue
    return frozenset(out)


class CapabilityRegistry:
    """Redis view of which lanes are alive and what they can solve.

    One instance per process is enough; every method is a thin awaitable over
    the shared async Redis client. A worker calls `heartbeat()` at boot and
    then on a timer shorter than `ttl`.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = DEFAULT_PREFIX,
        ttl: int = HEARTBEAT_TTL_SECONDS,
    ) -> None:
        self._r = redis
        self._prefix = prefix
        self._ttl = ttl

    async def heartbeat(
        self,
        lane: Lane,
        worker: str,
        types: Iterable[CaptchaType | str] = (),
    ) -> None:
        """Refresh this worker's key and declare the types it serves."""
        await self._r.setex(heartbeat_key(self._prefix, lane, worker), self._ttl, "1")
        members = [_type_value(t) for t in types]
        if members:
            await self._r.sadd(capability_key(self._prefix, lane), *members)

    async def worker_alive(self, lane: Lane, worker: str) -> bool:
        return bool(await self._r.exists(heartbeat_key(self._prefix, lane, worker)))

    async def live_lanes(self) -> tuple[Lane, ...]:
        """Lanes with at least one unexpired heartbeat key."""
        alive: list[Lane] = []
        for lane in LANES:
            pattern = f"{heartbeat_key(self._prefix, lane, '')}*"
            async for _key in self._r.scan_iter(match=pattern, count=100):
                alive.append(lane)
                break
        return tuple(alive)

    async def types_for_lane(self, lane: Lane) -> frozenset[CaptchaType]:
        return _parse_types(await self._r.smembers(capability_key(self._prefix, lane)))

    async def live_types(self) -> frozenset[CaptchaType]:
        """Union of capability sets of lanes that currently have a heartbeat."""
        types: set[CaptchaType] = set()
        for lane in await self.live_lanes():
            types |= await self.types_for_lane(lane)
        return frozenset(types)

    async def supports(self, ct: CaptchaType) -> bool:
        return ct in await self.live_types()

    async def clear(self, lane: Lane, worker: str) -> None:
        """Drop a worker on shutdown; drop the lane's cap set when it was last."""
        await self._r.delete(heartbeat_key(self._prefix, lane, worker))
        if lane not in await self.live_lanes():
            await self._r.delete(capability_key(self._prefix, lane))


async def gateway_live_types(redis: Redis, *, prefix: str = DEFAULT_PREFIX) -> frozenset[CaptchaType]:
    """Types with proof of a live worker, for the gateway's `solver_types`."""
    return await CapabilityRegistry(redis, prefix=prefix).live_types()


async def gateway_accepts(redis: Redis, ct: CaptchaType, *, prefix: str = DEFAULT_PREFIX) -> bool:
    """True when a fresh heartbeat advertises `ct` (contract §2/§7)."""
    return await CapabilityRegistry(redis, prefix=prefix).supports(ct)


async def gateway_ready(redis: Redis, *, prefix: str = DEFAULT_PREFIX) -> bool:
    """`/readyz` heartbeat check: at least one lane is alive."""
    return bool(await CapabilityRegistry(redis, prefix=prefix).live_lanes())
