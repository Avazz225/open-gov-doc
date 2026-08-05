from datetime import UTC, datetime, timedelta

from reporting_service.forensic import categorize_event_type, detect_download_anomalies


def test_categorize_event_type_download():
    assert categorize_event_type("document.downloaded") == "download"


def test_categorize_event_type_view():
    assert categorize_event_type("document.viewed") == "view"


def test_categorize_event_type_delete_variants():
    assert categorize_event_type("document.deleted") == "delete"
    assert categorize_event_type("document.force_deleted") == "delete"
    assert categorize_event_type("document.trash_purged") == "delete"


def test_categorize_event_type_falls_back_to_change():
    assert categorize_event_type("document.created") == "change"
    assert categorize_event_type("permission.approval.approved") == "change"
    assert categorize_event_type("document.metadata.updated") == "change"


def _dl(actor: str, when: datetime) -> dict:
    return {"event_type": "document.downloaded", "actor": actor, "occurred_at": when}


def test_detect_download_anomalies_flags_actor_over_threshold():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [_dl("alice", base + timedelta(seconds=i * 10)) for i in range(25)]

    anomalies = detect_download_anomalies(events, threshold_count=20, threshold_minutes=5)

    assert len(anomalies) == 1
    assert "alice" in anomalies[0]


def test_detect_download_anomalies_ignores_actor_under_threshold():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [_dl("bob", base + timedelta(seconds=i * 10)) for i in range(5)]

    anomalies = detect_download_anomalies(events, threshold_count=20, threshold_minutes=5)

    assert anomalies == []


def test_detect_download_anomalies_ignores_events_spread_outside_window():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # 25 Downloads, aber über 25 Stunden verteilt statt innerhalb von 5 Minuten.
    events = [_dl("carol", base + timedelta(hours=i)) for i in range(25)]

    anomalies = detect_download_anomalies(events, threshold_count=20, threshold_minutes=5)

    assert anomalies == []


def test_detect_download_anomalies_ignores_non_download_events():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        {
            "event_type": "document.viewed",
            "actor": "dave",
            "occurred_at": base + timedelta(seconds=i),
        }
        for i in range(30)
    ]

    anomalies = detect_download_anomalies(events, threshold_count=20, threshold_minutes=5)

    assert anomalies == []


def test_detect_download_anomalies_ignores_events_without_actor():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        {
            "event_type": "document.downloaded",
            "actor": None,
            "occurred_at": base + timedelta(seconds=i),
        }
        for i in range(30)
    ]

    anomalies = detect_download_anomalies(events, threshold_count=20, threshold_minutes=5)

    assert anomalies == []


def test_detect_download_anomalies_evaluates_actors_independently():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [_dl("alice", base + timedelta(seconds=i * 10)) for i in range(25)]
    events += [_dl("bob", base + timedelta(seconds=i * 10)) for i in range(3)]

    anomalies = detect_download_anomalies(events, threshold_count=20, threshold_minutes=5)

    assert len(anomalies) == 1
    assert "alice" in anomalies[0]
    assert "bob" not in anomalies[0]
