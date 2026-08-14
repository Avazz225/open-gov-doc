import random

# AWS standard formula ("Full Jitter", see their architecture blog post
# "Exponential Backoff And Jitter") - Post-Roadmap Phase 20 Session 1.
# `storage-service`'s `replication.py::process_pending` already has the
# attempt-counting/`failed_permanent` pattern (concept 3.6), but no backoff
# whatsoever between attempts - the same gap recurs in Phase 20 at four
# further places (archival, notification, rendering/ocr, federation-hub
# service). "Full Jitter" instead of "Equal Jitter"/plain exponential
# backoff: spreads simultaneously failed attempts as widely as possible
# across the time window, avoiding a renewed thundering-herd effect when
# many copies/deliveries fail in parallel, without enforcing an additional
# minimum wait time.
DEFAULT_BASE_SECONDS = 1.0
DEFAULT_CAP_SECONDS = 300.0


def compute_backoff_seconds(
    attempt: int,
    *,
    base: float = DEFAULT_BASE_SECONDS,
    cap: float = DEFAULT_CAP_SECONDS,
    rng: random.Random | None = None,
) -> float:
    """Full-jitter backoff: `random(0, min(cap, base * 2**attempt))`.

    `attempt` is the number of already-completed (failed) attempts,
    0-indexed - the first retry after the very first failure calls this
    with `attempt=0`. `rng` allows deterministic tests (e.g.
    `random.Random(seed)`) instead of the global `random` module."""
    if attempt < 0:
        raise ValueError("attempt darf nicht negativ sein")
    exponential = min(cap, base * (2**attempt))
    generator = rng if rng is not None else random
    return generator.uniform(0, exponential)
