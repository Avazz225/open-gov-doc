# dms-retry

Shared backoff/jitter math for retry poll loops (Post-Roadmap Phase 20 Session 1) - **no**
shared poll-loop framework, the project deliberately duplicates poll loops in a lightweight way (see
`docs/adr` from Phase 20 onward). Only the numeric formula is shared.

- `compute_backoff_seconds(attempt, *, base=1.0, cap=300.0, rng=None) -> float` - "Full Jitter"
  exponential backoff (`random(0, min(cap, base * 2**attempt))`, AWS standard formula). `attempt` is
  the number of attempts already made, 0-indexed. `rng` allows deterministic tests
  (`random.Random(seed)`).

## Usage

```python
from dms_retry import compute_backoff_seconds

delay = compute_backoff_seconds(copy.attempts)
next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
```

## Tests

```bash
uv run pytest libs/dms-retry/tests
```
