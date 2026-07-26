import time

from gateway_service.rate_limiter import RateLimiter


def test_allows_up_to_max_requests():
    limiter = RateLimiter(max_requests=3, window_seconds=60.0)

    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False


def test_tracks_clients_independently():
    limiter = RateLimiter(max_requests=1, window_seconds=60.0)

    assert limiter.allow("client-a") is True
    assert limiter.allow("client-b") is True
    assert limiter.allow("client-a") is False
    assert limiter.allow("client-b") is False


def test_window_expiry_frees_up_capacity():
    limiter = RateLimiter(max_requests=1, window_seconds=0.1)

    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False

    time.sleep(0.15)

    assert limiter.allow("client-a") is True
