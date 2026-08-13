import random

# AWS-Standardformel ("Full Jitter", siehe deren Architecture-Blog
# "Exponential Backoff And Jitter") - Post-Roadmap Phase 20 Session 1.
# `storage-service`s `replication.py::process_pending` hat bereits das
# Attempt-Zähl-/`failed_permanent`-Muster (Konzept 3.6), aber keinerlei
# Backoff zwischen Versuchen - dieselbe Lücke wiederholt sich in Phase 20
# an vier weiteren Stellen (archival-, notification-, rendering-/ocr-,
# federation-hub-service). "Full Jitter" statt "Equal Jitter"/reinem
# Exponential-Backoff: verteilt gleichzeitig fehlgeschlagene Versuche am
# breitesten über das Zeitfenster, vermeidet einen erneuten
# Thundering-Herd-Effekt bei vielen parallel fehlgeschlagenen Kopien/
# Zustellungen, ohne eine zusätzliche Mindestwartezeit zu erzwingen.
DEFAULT_BASE_SECONDS = 1.0
DEFAULT_CAP_SECONDS = 300.0


def compute_backoff_seconds(
    attempt: int,
    *,
    base: float = DEFAULT_BASE_SECONDS,
    cap: float = DEFAULT_CAP_SECONDS,
    rng: random.Random | None = None,
) -> float:
    """Full-Jitter-Backoff: `random(0, min(cap, base * 2**attempt))`.

    `attempt` ist die Anzahl bereits erfolgter (fehlgeschlagener) Versuche,
    0-indiziert - der erste Retry nach dem allerersten Fehlschlag ruft dies
    mit `attempt=0` auf. `rng` erlaubt deterministische Tests (z. B.
    `random.Random(seed)`) statt des globalen `random`-Moduls."""
    if attempt < 0:
        raise ValueError("attempt darf nicht negativ sein")
    exponential = min(cap, base * (2**attempt))
    generator = rng if rng is not None else random
    return generator.uniform(0, exponential)
