import time
from collections import deque


class RateLimiter:
    """Sliding-Window-Rate-Limiting je Client (3.5) - rein in-process, ohne
    geteilten Store. Ausreichend für eine einzelne Gateway-Instanz; sobald das
    Gateway horizontal skaliert wird, braucht es einen gemeinsamen Zähler
    (z. B. Redis) statt dieser lokalen Datenstruktur (siehe ADR 0005).
    """

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True
