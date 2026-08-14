import time
import uuid

from redis import asyncio as redis


class RateLimiter:
    """Sliding-window rate limiting per client (3.5) - shared Redis store
    instead of an in-process `dict` (see ADR 0005 for the original
    in-process design and ADR 0097 for this migration): multiple
    horizontally scaled gateway instances see the same counter per client
    key, instead of each maintaining its own, independent limit.

    Sliding window via sorted set (`ZADD`/`ZREMRANGEBYSCORE`/`ZCARD`)
    instead of the simpler fixed-window variant (`INCR`+`EXPIRE`): a fixed
    window briefly allows up to 2x `max_requests` at the window edge (the
    end of window N and the start of window N+1 coincide in time for a
    client) - a real weakness for login protection (brute force, also
    applies to public routes, see `docs/services/gateway-service.md`). The
    price for this is one sorted-set member per request instead of a
    single counter - negligible for the windows typical here (default 600
    requests/60s per client) compared to the cleaner guarantee.
    """

    def __init__(self, *, redis_url: str, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis = redis.from_url(redis_url, decode_responses=True)

    async def allow(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        redis_key = f"gateway:rate-limit:{key}"
        # uuid suffix instead of a plain timestamp as the member: two
        # requests within the same floating-point resolution must not
        # overwrite each other in the sorted set (otherwise Redis would
        # count them as a single member).
        member = f"{now:.6f}:{uuid.uuid4().hex}"

        # A single MULTI/EXEC transaction: remove old entries, add the new
        # one, then count - the new entry is already included in the count
        # result. If the limit is exceeded, it is immediately removed again
        # via ZREM right after (see below), instead of adding it
        # conditionally up front - Redis has no conditional ZADD "only if
        # ZCARD afterward <= max_requests", a server-side Lua script would
        # be the only alternative, deliberately not chosen here (more
        # moving parts for the same effect).
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(redis_key, 0, window_start)
            pipe.zadd(redis_key, {member: now})
            pipe.zcard(redis_key)
            # TTL as a safety net in case a client disappears permanently
            # (no further request would ever clean up the key otherwise) -
            # slightly more generous than the window itself, so a request
            # running close to the edge doesn't prematurely lose its
            # history.
            pipe.expire(redis_key, int(self.window_seconds) + 1)
            _, _, count, _ = await pipe.execute()

        if count > self.max_requests:
            await self._redis.zrem(redis_key, member)
            return False
        return True

    async def aclose(self) -> None:
        await self._redis.aclose()
