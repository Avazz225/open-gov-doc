import asyncio
import os
import uuid

import pytest
from gateway_service.rate_limiter import RateLimiter

# Echter Redis-Container aus infra/docker-compose.yml (seit P25-S3), kein
# Mock/Fake - gleiche Teststrategie wie gegen Postgres/NATS/MinIO überall
# sonst in diesem Projekt (siehe CONTRIBUTING.md).
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def make_limiter():
    """Fabrik statt einer einzelnen Fixture-Instanz - mehrere Tests brauchen
    mehrere, unabhängig konstruierte `RateLimiter`, die trotzdem denselben
    Redis-Key teilen (steht für mehrere Gateway-Replikas, siehe
    `test_state_is_shared_across_separate_limiter_instances` unten)."""
    limiters: list[RateLimiter] = []

    def _make(*, max_requests: int, window_seconds: float) -> RateLimiter:
        limiter = RateLimiter(
            redis_url=REDIS_URL, max_requests=max_requests, window_seconds=window_seconds
        )
        limiters.append(limiter)
        return limiter

    yield _make

    for limiter in limiters:
        await limiter.aclose()


def _unique_key(prefix: str) -> str:
    # Eigener, per Test eindeutiger Client-Schlüssel statt eines festen
    # Namens - Tests laufen gegen denselben, dauerhaft laufenden Redis wie
    # jeder andere Testlauf (auch zweimal hintereinander, siehe
    # CONTRIBUTING.md-Definition-of-Done) und dürfen sich keine Zähler-Reste
    # aus einem vorherigen Lauf teilen.
    return f"test-{prefix}-{uuid.uuid4().hex[:10]}"


async def test_allows_up_to_max_requests(make_limiter):
    limiter = make_limiter(max_requests=3, window_seconds=60.0)
    key = _unique_key("client")

    assert await limiter.allow(key) is True
    assert await limiter.allow(key) is True
    assert await limiter.allow(key) is True
    assert await limiter.allow(key) is False


async def test_tracks_clients_independently(make_limiter):
    limiter = make_limiter(max_requests=1, window_seconds=60.0)
    client_a = _unique_key("client-a")
    client_b = _unique_key("client-b")

    assert await limiter.allow(client_a) is True
    assert await limiter.allow(client_b) is True
    assert await limiter.allow(client_a) is False
    assert await limiter.allow(client_b) is False


async def test_window_expiry_frees_up_capacity(make_limiter):
    limiter = make_limiter(max_requests=1, window_seconds=0.2)
    key = _unique_key("client")

    assert await limiter.allow(key) is True
    assert await limiter.allow(key) is False

    await asyncio.sleep(0.3)

    assert await limiter.allow(key) is True


async def test_state_is_shared_across_separate_limiter_instances(make_limiter):
    """Kernpunkt der Umstellung von P25-S3 (siehe ADR 0097): zwei getrennt
    konstruierte `RateLimiter`-Instanzen - steht hier für zwei horizontal
    skalierte Gateway-Replikas - teilen sich denselben Redis-Zähler für
    denselben Client-Schlüssel. Beim alten in-process `dict` (siehe Git-
    Historie dieser Datei) hätte jede Instanz ihr eigenes, unabhängiges
    Limit gesehen - ein Client hätte das Limit durch Verteilung über beide
    Instanzen faktisch verdoppeln können."""
    key = _unique_key("client")
    limiter_a = make_limiter(max_requests=2, window_seconds=60.0)
    limiter_b = make_limiter(max_requests=2, window_seconds=60.0)

    assert await limiter_a.allow(key) is True
    assert await limiter_b.allow(key) is True
    assert await limiter_a.allow(key) is False
    assert await limiter_b.allow(key) is False
