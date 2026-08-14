import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "host",
    }
)


def filter_headers(headers) -> dict[str, str]:
    """Removes hop-by-hop headers AND any ``X-DMS-*`` header sent by the
    client (security finding, P14-S11 live verification): these headers
    are reserved exclusively for the JWT-derived identity injected by the
    gateway itself (``X-DMS-Principal``/`-Username`/`-Roles`/
    `-Maintenance-Active`) - without this filter, a client with a valid
    bearer token could send its own ``X-DMS-Principal`` header, which
    would NOT be overwritten but would end up as an additional,
    differently-cased dict entry alongside the real one (Python dict keys
    are case-sensitive, ``"x-dms-principal"`` from the ASGI-normalized
    incoming headers and ``"X-DMS-Principal"`` from ``proxy()``'s
    ``identity_headers`` are two different keys) - both headers were
    thereby passed on to the downstream service, and the value actually
    used depended on the respective HTTP parser. Verified live: an
    authenticated caller could impersonate any other principal this way.
    Affected every endpoint that evaluates ``X-DMS-Principal``/`-Roles`
    (among others, P5e-S2 reference-number admin role, P14-S6 teamspace
    membership, P14-S10 share-link creator, P14-S11 delegation) - see
    ADR 0049."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS and not k.lower().startswith("x-dms-")
    }


class InstanceResolver:
    """Queries the registry for active instances of each `service_type`
    (3.2a) and caches the result briefly (`instance_cache_ttl_seconds`), so
    not every proxied request costs an additional registry round trip. If
    the registry call fails, the last known state continues to be used
    instead of immediately returning 503 (brief registry outages should
    not immediately cripple ongoing operation).
    """

    def __init__(
        self, *, client: httpx.AsyncClient, registry_base_url: str, cache_ttl_seconds: float
    ) -> None:
        self._client = client
        self._registry_base_url = registry_base_url.rstrip("/")
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        # Workload-aware selection (P25-S4): counter of open requests per
        # instance address, purely in-process (see `pick()`/`reserve()`/
        # `release()` below). Deliberately NOT shared via Redis like the
        # rate limiter (P25-S3, ADR 0097) - this counter is meant to
        # reflect the requests actually currently open toward each target
        # FROM THIS gateway replica, not an installation-wide value. With
        # multiple gateway replicas, each replica sees only its own subset
        # of the load, see docs/services/gateway-service.md.
        self._open_requests: dict[str, int] = {}

    async def resolve(self, service_type: str) -> list[dict]:
        now = time.monotonic()
        cached = self._cache.get(service_type)
        if cached is not None and now - cached[0] < self._cache_ttl:
            return cached[1]

        try:
            response = await self._client.get(f"{self._registry_base_url}/instances/{service_type}")
            response.raise_for_status()
            # Drain mechanism (10.5/3.8, P10-S2): a "draining" instance
            # remains reachable (ongoing operations complete normally), but
            # no longer receives NEW requests - it drops out of the
            # selection pool here, instead of being separately queried or
            # killed.
            instances = [
                i for i in response.json() if i["healthy"] and i.get("status", "active") == "active"
            ]
        except httpx.HTTPError:
            instances = cached[1] if cached is not None else []

        self._cache[service_type] = (now, instances)
        return instances

    def pick(self, instances: list[dict]) -> dict:
        """Selects the instance with the fewest currently open requests
        (P25-S4) instead of purely at random as before (ADR 0005).
        Instances without a prior reservation count as 0. Tie-break among
        multiple instances with the same minimum: random among the
        minimum candidates instead of, e.g., always the first in the list
        - especially at startup (all counters at 0), "first in the list"
        would otherwise send every request to the same instance until it
        is the first to have an open request, instead of spreading the
        load evenly from the start.
        """
        min_open = min(self._open_requests.get(i["address"], 0) for i in instances)
        candidates = [i for i in instances if self._open_requests.get(i["address"], 0) == min_open]
        return random.choice(candidates)

    def reserve(self, instance: dict) -> None:
        """Increments the counter of open requests for `instance` by one -
        to be called before the actual upstream call, see
        `reserved_instance()` below for the recommended usage (also covers
        exceptions)."""
        address = instance["address"]
        self._open_requests[address] = self._open_requests.get(address, 0) + 1

    def release(self, instance: dict) -> None:
        """Counterpart to `reserve()` - to be called after the upstream
        call completes (success OR exception). `max(0, ...)` as protection
        against dropping below 0 in case of an unexpected reserve/release
        imbalance, instead of leaving the counter permanently negative."""
        address = instance["address"]
        self._open_requests[address] = max(0, self._open_requests.get(address, 0) - 1)

    @asynccontextmanager
    async def reserved_instance(self, instances: list[dict]) -> AsyncIterator[dict]:
        """Selects an instance (`pick()`) and holds it reserved as "open"
        for the duration of the `with` block - `release()` runs in a
        `finally`, so it also covers the case where the actual upstream
        call aborts with an exception (e.g. `httpx.HTTPError`). Without
        this `finally`, a failing upstream call would leave its reserved
        slot permanently occupied (a "leak"), causing this instance to be
        incorrectly considered permanently busy and never preferentially
        selected again."""
        instance = self.pick(instances)
        self.reserve(instance)
        try:
            yield instance
        finally:
            self.release(instance)


class MaintenanceStateClient:
    """Queries the maintenance-mode status (4.8, P6-S6) directly from
    `permission-service` - via the same `InstanceResolver` as every
    proxied request, since the gateway does not know a fixed service URL
    (everything goes through the registry). Short caching
    (`cache_ttl_seconds`) as with `InstanceResolver`, for the same reason:
    not every single request should cost an additional round trip. If the
    query fails (`permission-service` unreachable), it is **not** assumed
    to be active (fail-open) - a briefly unreachable `permission-service`
    should not accidentally cripple the entire system, that would be an
    availability own-goal, not a security measure."""

    def __init__(
        self, *, client: httpx.AsyncClient, resolver: InstanceResolver, cache_ttl_seconds: float
    ) -> None:
        self._client = client
        self._resolver = resolver
        self._cache_ttl = cache_ttl_seconds
        self._cached_at: float = 0.0
        self._cached_active: bool = False

    async def is_active(self) -> bool:
        now = time.monotonic()
        if now - self._cached_at < self._cache_ttl:
            return self._cached_active

        try:
            instances = await self._resolver.resolve("permission-service")
            if not instances:
                return self._cached_active
            instance = self._resolver.pick(instances)
            response = await self._client.get(f"{instance['address'].rstrip('/')}/maintenance-mode")
            response.raise_for_status()
            self._cached_active = response.json()["active"]
        except httpx.HTTPError:
            pass
        self._cached_at = now
        return self._cached_active
