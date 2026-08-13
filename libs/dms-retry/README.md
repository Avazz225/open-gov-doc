# dms-retry

Geteilte Backoff-/Jitter-Mathematik für Retry-Poll-Loops (Post-Roadmap Phase 20 Session 1) - **kein**
gemeinsamer Poll-Loop-Rahmen, das Projekt dupliziert Poll-Loops bewusst leichtgewichtig (siehe
`docs/adr` ab Phase 20). Nur die Zahlenformel ist geteilt.

- `compute_backoff_seconds(attempt, *, base=1.0, cap=300.0, rng=None) -> float` - "Full Jitter"
  Exponentiell-Backoff (`random(0, min(cap, base * 2**attempt))`, AWS-Standardformel). `attempt` ist
  die Anzahl bereits erfolgter Versuche, 0-indiziert. `rng` erlaubt deterministische Tests
  (`random.Random(seed)`).

## Nutzung

```python
from dms_retry import compute_backoff_seconds

delay = compute_backoff_seconds(copy.attempts)
next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
```

## Tests

```bash
uv run pytest libs/dms-retry/tests
```
