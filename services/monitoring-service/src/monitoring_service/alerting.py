"""Pure alert evaluation (P88-S5, ADR 0245) - no I/O here; `alert_store.py`
persists, `main.py` scrapes and runs the tick.

A rule names a sensor, an optional label filter, an aggregation and up to
four thresholds with separate warning/error repeat counts, the scheme of the
reference system the user compared against."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

GAUGE_AGGREGATIONS = ("max", "min", "sum", "avg")
RATE_AGGREGATION = "rate_per_minute"
AGGREGATIONS = (*GAUGE_AGGREGATIONS, RATE_AGGREGATION)

LEVELS = ("ok", "warning", "error", "unknown")
ALARM_LEVELS = ("warning", "error")


@dataclass(frozen=True)
class Thresholds:
    min_error: float | None = None
    min_warning: float | None = None
    max_warning: float | None = None
    max_error: float | None = None

    def ordered(self) -> list[float]:
        return [
            v
            for v in (self.min_error, self.min_warning, self.max_warning, self.max_error)
            if v is not None
        ]


def validate_rule(
    thresholds: Thresholds, aggregation: str, warning_repeat: int, error_repeat: int
) -> None:
    """Raises ValueError with a German message for an unusable rule."""
    if aggregation not in AGGREGATIONS:
        raise ValueError(f"Unbekannte Aggregation {aggregation!r} - erlaubt: {list(AGGREGATIONS)}")
    values = thresholds.ordered()
    if not values:
        raise ValueError("Mindestens ein Schwellwert ist nötig")
    if values != sorted(values):
        raise ValueError(
            "Schwellwerte müssen aufsteigend sein: "
            "min_error ≤ min_warning ≤ max_warning ≤ max_error"
        )
    if warning_repeat < 1 or error_repeat < 1:
        raise ValueError("Wiederholungen müssen mindestens 1 sein")


def family_name(sensor: str) -> str:
    """The Prometheus family name of a dotted sensor name - the same mapping
    `dms_metrics_client.SensorRegistry._prom_name` uses."""
    return sensor.replace(".", "_").replace("-", "_")


def _matches(labels: dict[str, str], label_filter: dict[str, str]) -> bool:
    return all(labels.get(key) == value for key, value in label_filter.items())


def sample_values(
    families: dict, sensor: str, aggregation: str, label_filter: dict[str, str]
) -> list[float]:
    """The values a rule aggregates. Gauges: the family's own samples.
    Rates: the cumulative `_total` (counter) or `_count` (histogram) samples
    - their `_created`/`_bucket`/`_sum` siblings are skipped."""
    family = families.get(family_name(sensor))
    if family is None:
        return []
    if aggregation == RATE_AGGREGATION:
        wanted = {f"{family.name}_total", f"{family.name}_count"}
        if family.type == "gauge":
            wanted = {family.name}
    else:
        wanted = {family.name}
    return [
        float(sample.value)
        for sample in family.samples
        if sample.name in wanted and _matches(sample.labels, label_filter)
    ]


@dataclass(frozen=True)
class Measurement:
    value: float | None
    total: float | None = None  # rate rules: the summed total to remember


def measure(
    values: list[float],
    aggregation: str,
    *,
    last_total: float | None,
    last_at: datetime | None,
    now: datetime,
) -> Measurement:
    """`None` value = unknown (no samples, or a rate's first observation)."""
    if not values:
        return Measurement(None)
    if aggregation == RATE_AGGREGATION:
        total = sum(values)
        if last_total is None or last_at is None:
            return Measurement(None, total)
        minutes = (now - last_at).total_seconds() / 60
        if minutes <= 0:
            return Measurement(None, total)
        # A smaller total means a counter reset (restarted instance): the new
        # total is the increase since then.
        increase = total - last_total if total >= last_total else total
        return Measurement(increase / minutes, total)
    if aggregation == "max":
        return Measurement(max(values))
    if aggregation == "min":
        return Measurement(min(values))
    if aggregation == "sum":
        return Measurement(sum(values))
    return Measurement(sum(values) / len(values))


@dataclass(frozen=True)
class LevelResult:
    level: str
    warning_streak: int
    error_streak: int


def next_level(
    value: float | None,
    thresholds: Thresholds,
    *,
    warning_repeat: int,
    error_repeat: int,
    warning_streak: int,
    error_streak: int,
) -> LevelResult:
    """ADR 0245 Decision 3: an error breach counts towards both streaks; a
    level needs its streak to reach its repeat count; a value back in range
    is ok at once. Unknown keeps the streaks (no evidence either way)."""
    if value is None:
        return LevelResult("unknown", warning_streak, error_streak)
    t = thresholds
    error_breach = (t.min_error is not None and value < t.min_error) or (
        t.max_error is not None and value > t.max_error
    )
    warning_breach = error_breach or (
        (t.min_warning is not None and value < t.min_warning)
        or (t.max_warning is not None and value > t.max_warning)
    )
    error_streak = error_streak + 1 if error_breach else 0
    warning_streak = warning_streak + 1 if warning_breach else 0
    if error_breach and error_streak >= error_repeat:
        level = "error"
    elif warning_breach and warning_streak >= warning_repeat:
        level = "warning"
    else:
        level = "ok"
    return LevelResult(level, warning_streak, error_streak)


def notification_due(level: str, notified_level: str | None) -> str | None:
    """The event to publish for notification, or None (Decision 3): an alarm
    level differing from what was last notified raises; ok after a notified
    alarm resolves; unknown and an initial ok notify nothing."""
    if level in ALARM_LEVELS and level != notified_level:
        return "monitoring.alert.raised"
    if level == "ok" and notified_level in ALARM_LEVELS:
        return "monitoring.alert.resolved"
    return None


def _parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def window_active(
    *,
    enabled: bool,
    starts_at: datetime | None,
    ends_at: datetime | None,
    weekdays: list[int] | None,
    daily_start: str | None,
    daily_end: str | None,
    now: datetime,
    timezone: str,
) -> bool:
    """Decision 5: an optional absolute range, optional weekdays (0 = Monday)
    and an optional daily time range in the installation's time zone; an end
    before the start spans midnight (the weekday is the start's)."""
    if not enabled:
        return False
    if starts_at is not None and now < starts_at:
        return False
    if ends_at is not None and now >= ends_at:
        return False
    local = now.astimezone(ZoneInfo(timezone))
    if daily_start and daily_end:
        start, end = _parse_time(daily_start), _parse_time(daily_end)
        clock = local.time()
        if start <= end:
            if not (start <= clock < end):
                return False
            day = local.weekday()
        elif clock >= start:
            day = local.weekday()
        elif clock < end:
            day = (local.weekday() - 1) % 7
        else:
            return False
    else:
        day = local.weekday()
    return not weekdays or day in weekdays


def validate_time(value: str | None) -> None:
    if value is None:
        return
    try:
        _parse_time(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Uhrzeit {value!r} - erwartet HH:MM") from exc
