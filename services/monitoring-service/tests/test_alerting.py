"""Pure alert evaluation (P88-S5, ADR 0245)."""

from datetime import UTC, datetime, timedelta

import pytest
from monitoring_service import alerting
from prometheus_client.parser import text_string_to_metric_families

T = alerting.Thresholds(min_error=0, min_warning=5, max_warning=80, max_error=95)
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)  # a Monday, 11:00 in Berlin


def families(text: str) -> dict:
    return {f.name: f for f in text_string_to_metric_families(text)}


EXPOSITION = """# TYPE storage_target_used_percent gauge
storage_target_used_percent{target="local",service="storage-service"} 85.0
storage_target_used_percent{target="archive",service="storage-service"} 40.0
# TYPE auth_logins_total counter
auth_logins_total{result="rejected",service="auth-service"} 12.0
auth_logins_total{result="success",service="auth-service"} 30.0
# TYPE auth_logins_created gauge
auth_logins_created{result="success",service="auth-service"} 1.0
# TYPE document_upload_duration histogram
document_upload_duration_bucket{le="+Inf",service="document-service"} 7.0
document_upload_duration_count{service="document-service"} 7.0
document_upload_duration_sum{service="document-service"} 3.5
"""


def step(value, ws=0, es=0, warning_repeat=1, error_repeat=1):
    return alerting.next_level(
        value,
        T,
        warning_repeat=warning_repeat,
        error_repeat=error_repeat,
        warning_streak=ws,
        error_streak=es,
    )


def test_levels_for_each_band():
    assert step(50).level == "ok"
    assert step(85).level == "warning"
    assert step(99).level == "error"
    assert step(3).level == "warning"
    assert step(-1).level == "error"
    assert step(None).level == "unknown"


def test_repeat_counts_debounce_and_an_unrepeated_error_shows_as_warning():
    first = step(99, warning_repeat=1, error_repeat=3)
    assert (first.level, first.warning_streak, first.error_streak) == ("warning", 1, 1)
    second = step(99, first.warning_streak, first.error_streak, 1, 3)
    third = step(99, second.warning_streak, second.error_streak, 1, 3)
    assert (second.level, third.level) == ("warning", "error")
    # Back in range: ok at once, streaks reset.
    back = step(50, third.warning_streak, third.error_streak, 1, 3)
    assert (back.level, back.warning_streak, back.error_streak) == ("ok", 0, 0)


def test_a_single_outlier_does_not_warn_with_repeat_two():
    assert step(85, warning_repeat=2).level == "ok"
    assert step(85, ws=1, warning_repeat=2).level == "warning"


def test_unknown_keeps_the_streaks():
    result = step(None, ws=2, es=1)
    assert (result.level, result.warning_streak, result.error_streak) == ("unknown", 2, 1)


def test_gauge_aggregation_with_label_filter():
    fams = families(EXPOSITION)
    values = alerting.sample_values(fams, "storage.target.used_percent", "max", {})
    assert sorted(values) == [40.0, 85.0]
    only_local = alerting.sample_values(
        fams, "storage.target.used_percent", "max", {"target": "local"}
    )
    assert only_local == [85.0]
    for aggregation, expected in (("max", 85.0), ("min", 40.0), ("sum", 125.0), ("avg", 62.5)):
        m = alerting.measure(values, aggregation, last_total=None, last_at=None, now=NOW)
        assert m.value == expected


def test_rate_per_minute_from_counters_and_histograms():
    fams = families(EXPOSITION)
    logins = alerting.sample_values(fams, "auth.logins", "rate_per_minute", {"result": "rejected"})
    assert logins == [12.0]  # the `_created` family is a separate gauge
    first = alerting.measure(logins, "rate_per_minute", last_total=None, last_at=None, now=NOW)
    assert (first.value, first.total) == (None, 12.0)  # needs a previous total
    later = alerting.measure(
        [18.0], "rate_per_minute", last_total=12.0, last_at=NOW - timedelta(minutes=2), now=NOW
    )
    assert later.value == 3.0
    reset = alerting.measure(
        [4.0], "rate_per_minute", last_total=12.0, last_at=NOW - timedelta(minutes=1), now=NOW
    )
    assert reset.value == 4.0  # counter reset: the new total is the increase
    uploads = alerting.sample_values(fams, "document.upload.duration", "rate_per_minute", {})
    assert uploads == [7.0]  # the histogram's _count


def test_missing_sensor_is_unknown():
    values = alerting.sample_values(families(EXPOSITION), "does.not.exist", "max", {})
    assert alerting.measure(values, "max", last_total=None, last_at=None, now=NOW).value is None


def test_notification_follows_the_level():
    assert alerting.notification_due("warning", None) == "monitoring.alert.raised"
    assert alerting.notification_due("error", "warning") == "monitoring.alert.raised"
    assert alerting.notification_due("warning", "warning") is None
    assert alerting.notification_due("ok", "error") == "monitoring.alert.resolved"
    assert alerting.notification_due("ok", None) is None  # an initial ok isn't mailed
    assert alerting.notification_due("unknown", "error") is None


def active(**kwargs):
    defaults = dict(
        enabled=True,
        starts_at=None,
        ends_at=None,
        weekdays=None,
        daily_start=None,
        daily_end=None,
        now=NOW,
        timezone="Europe/Berlin",
    )
    return alerting.window_active(**{**defaults, **kwargs})


def test_downtime_windows():
    assert active(starts_at=NOW - timedelta(hours=1), ends_at=NOW + timedelta(hours=1))
    assert not active(starts_at=NOW + timedelta(hours=1))
    assert not active(starts_at=NOW - timedelta(hours=1), ends_at=NOW, enabled=True)
    assert not active(enabled=False, starts_at=NOW - timedelta(hours=1))
    # Weekly: Mondays 10:30-12:00 local (11:00 Berlin now).
    assert active(weekdays=[0], daily_start="10:30", daily_end="12:00")
    assert not active(weekdays=[1], daily_start="10:30", daily_end="12:00")
    assert not active(weekdays=[0], daily_start="12:00", daily_end="13:00")
    # Overnight: Sunday 22:00 -> Monday 02:00 covers Monday 01:00 Berlin.
    monday_night = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)  # 01:00 Berlin
    assert active(weekdays=[6], daily_start="22:00", daily_end="02:00", now=monday_night)
    assert not active(weekdays=[0], daily_start="22:00", daily_end="02:00", now=monday_night)


@pytest.mark.parametrize(
    "thresholds,aggregation,repeats",
    [
        (alerting.Thresholds(), "max", (1, 1)),
        (alerting.Thresholds(max_warning=90, max_error=80), "max", (1, 1)),
        (alerting.Thresholds(max_error=1), "median", (1, 1)),
        (alerting.Thresholds(max_error=1), "max", (0, 1)),
    ],
)
def test_invalid_rules_are_refused(thresholds, aggregation, repeats):
    with pytest.raises(ValueError):
        alerting.validate_rule(thresholds, aggregation, *repeats)
