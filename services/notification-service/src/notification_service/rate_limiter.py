import time
from collections import defaultdict


class RecipientRateLimiter:
    """In-process sliding-window rate limit per recipient (Post-Roadmap
    Phase 38 Session 2) - defense in depth against a misconfigured/fast-
    cycling caller repeatedly notifying the same recipient, on top of the
    RBAC fix that already restricts callers to `notification.write`
    holders. In-process (a plain dict, not Redis-backed like
    gateway-service's, ADR 0097) since this service runs as a single
    instance - same rationale as gateway-service's own pre-ADR-0097 design
    (ADR 0005): a shared store only earns its complexity once multiple
    replicas need to agree on one counter."""

    def __init__(self, settings) -> None:
        # `settings` object kept (not the two values captured as plain
        # primitives) so a test can mutate
        # `settings.notification_rate_limit_max_per_recipient` at runtime
        # and see it take effect immediately - same convention already
        # established for `settings.max_notification_attempts`
        # (`repository.create_and_send`, see `test_api.py`).
        self._settings = settings
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, recipient: str) -> bool:
        now = time.monotonic()
        window_start = now - self._settings.notification_rate_limit_window_seconds
        hits = [t for t in self._hits[recipient] if t > window_start]
        if len(hits) >= self._settings.notification_rate_limit_max_per_recipient:
            self._hits[recipient] = hits
            return False
        hits.append(now)
        self._hits[recipient] = hits
        return True
