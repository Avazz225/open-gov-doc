"""Alert rules, ticks and downtime windows through the app (P88-S5, ADR 0245)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from monitoring_service import main
from monitoring_service.main import app
from prometheus_client.parser import text_string_to_metric_families

ADMIN = {"x-dms-principal": "alice"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        app.state.auth_client = AsyncMock()
        app.state.auth_client.get_active_superuser.return_value = (False, None)
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        app.state.registry_client = AsyncMock()
        app.state.registry_client.list_active_instances.return_value = []
        yield c


@pytest.fixture
def published(monkeypatch):
    events = []

    async def record(event_type, subject, payload, *, actor=None):
        events.append((event_type, payload, actor))

    monkeypatch.setattr(main, "publish_event", record)
    return events


def used(percent: float) -> dict:
    text = (
        "# TYPE storage_target_used_percent gauge\n"
        f'storage_target_used_percent{{target="local",service="storage-service"}} {percent}\n'
    )
    return {f.name: f for f in text_string_to_metric_families(text)}


RULE = {
    "name": "Speicher voll",
    "sensor": "storage.target.used_percent",
    "label_filter": {"target": "local"},
    "aggregation": "max",
    "max_warning": 80,
    "max_error": 95,
    "warning_repeat": 1,
    "error_repeat": 2,
}


def tick(client, percent):
    return client.portal.call(main.run_alert_tick, used(percent))


def rule_state(client):
    return client.get("/alert-rules").json()[0]


def test_writes_need_admin_monitoring(client):
    app.state.permission_client.has_permission.return_value = False
    assert client.post("/alert-rules", json=RULE, headers=ADMIN).status_code == 403
    assert client.post("/alert-rules", json=RULE).status_code == 403
    assert client.get("/alert-rules").status_code == 200  # reads are open


def test_invalid_rules_are_422(client):
    bad = {**RULE, "max_warning": 99}
    assert client.post("/alert-rules", json=bad, headers=ADMIN).status_code == 422
    none = {**RULE, "max_warning": None, "max_error": None}
    assert client.post("/alert-rules", json=none, headers=ADMIN).status_code == 422


def test_a_rule_raises_escalates_and_resolves_with_one_mail_per_level(client, published):
    created = client.post("/alert-rules", json=RULE, headers=ADMIN)
    assert created.status_code == 201
    published.clear()

    tick(client, 50)
    assert rule_state(client)["level"] == "ok"
    tick(client, 85)
    assert rule_state(client)["level"] == "warning"
    tick(client, 99)  # error needs two in a row: still warning, no new mail
    assert rule_state(client)["level"] == "warning"
    tick(client, 99)
    assert rule_state(client)["level"] == "error"
    tick(client, 60)
    state = rule_state(client)
    assert (state["level"], state["notified_level"]) == ("ok", "ok")

    mails = [(t, p["level"]) for t, p, _ in published if t.endswith(("raised", "resolved"))]
    assert mails == [
        ("monitoring.alert.raised", "warning"),
        ("monitoring.alert.raised", "error"),
        ("monitoring.alert.resolved", "ok"),
    ]
    changes = [p["level"] for t, p, _ in published if t == "monitoring.alert.state_changed"]
    assert changes == ["ok", "warning", "error", "ok"]  # unknown -> ok on the first tick
    assert all(actor == "system:monitoring-alerting" for _, _, actor in published)


def test_a_downtime_window_defers_the_mail_until_it_ends(client, published):
    client.post("/alert-rules", json={**RULE, "error_repeat": 1}, headers=ADMIN)
    now = datetime.now(UTC)
    window = client.post(
        "/downtime-windows",
        json={
            "name": "Wartung",
            "starts_at": (now - timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
        },
        headers=ADMIN,
    ).json()
    assert window["active"] is True
    published.clear()

    tick(client, 99)
    state = rule_state(client)
    assert (state["level"], state["notified_level"], state["suppressed"]) == ("error", None, True)
    assert not [t for t, _, _ in published if t == "monitoring.alert.raised"]

    client.delete(f"/downtime-windows/{window['id']}", headers=ADMIN)
    tick(client, 99)
    raised = [p for t, p, _ in published if t == "monitoring.alert.raised"]
    assert [p["level"] for p in raised] == ["error"]


def test_an_inactive_sensor_is_unknown_and_not_mailed(client, published):
    client.post("/alert-rules", json=RULE, headers=ADMIN)
    client.portal.call(main.run_alert_tick, {})
    state = rule_state(client)
    assert (state["level"], state["value"]) == ("unknown", None)
    assert not [t for t, _, _ in published if t.startswith("monitoring.alert.r")]


def test_a_second_replica_skips_the_tick_while_the_lock_is_held(client):
    """Decision 4: hold the advisory lock in another transaction."""
    from monitoring_service import alert_store
    from sqlalchemy import text

    async def hold_and_tick():
        async with app.state.session_factory() as holder:
            await holder.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": alert_store.ALERT_LOCK_KEY}
            )
            result = await main.run_alert_tick(used(50))
            await holder.rollback()
            return result

    assert client.portal.call(hold_and_tick) is None


def test_prometheus_export(client):
    client.post("/alert-rules", json=RULE, headers=ADMIN)
    rate_rule = {
        "name": "Fehlanmeldungen",
        "sensor": "auth.logins",
        "label_filter": {"result": "rejected"},
        "aggregation": "rate_per_minute",
        "max_warning": 10,
    }
    client.post("/alert-rules", json=rate_rule, headers=ADMIN)
    body = client.get("/alert-rules/prometheus").text
    assert body.startswith("groups:")
    assert 'max(storage_target_used_percent{target=\\"local\\"})) > 80' in body
    assert "severity: critical" in body
    assert "for: 60s" in body  # error_repeat 2 x 60 s interval, minus the first
    assert 'sum(rate(auth_logins_total{result=\\"rejected\\"}[5m])) * 60' in body


def test_downtime_window_validation(client):
    assert client.post("/downtime-windows", json={"name": "leer"}, headers=ADMIN).status_code == 422
    half = {"name": "x", "weekdays": [0], "daily_start": "22:00"}
    assert client.post("/downtime-windows", json=half, headers=ADMIN).status_code == 422
    bad_time = {"name": "x", "daily_start": "25:00", "daily_end": "02:00"}
    assert client.post("/downtime-windows", json=bad_time, headers=ADMIN).status_code == 422
    weekly = {"name": "Nacht", "weekdays": [6], "daily_start": "22:00", "daily_end": "02:00"}
    assert client.post("/downtime-windows", json=weekly, headers=ADMIN).status_code == 201


def test_preview_shows_value_level_and_matching_series(client, monkeypatch):
    """P88-S6: a draft against a fresh scrape, nothing persisted."""

    async def fake_scrape():
        return used(85)

    monkeypatch.setattr(main, "_scrape_families", fake_scrape)
    preview = client.post("/alert-rules/preview", json=RULE).json()
    assert preview["value"] == 85.0
    assert preview["level"] == "warning"
    assert preview["series"] == [
        {"labels": {"target": "local", "service": "storage-service"}, "value": 85.0}
    ]
    assert client.get("/alert-rules").json() == []

    missing = client.post("/alert-rules/preview", json={**RULE, "sensor": "nope"}).json()
    assert (missing["value"], missing["reason"]) == (None, "no_samples")
    rate = client.post(
        "/alert-rules/preview", json={**RULE, "aggregation": "rate_per_minute"}
    ).json()
    assert rate["reason"] == "rate_needs_history"


def _alert_levels(client) -> dict[str, float]:
    body = client.get("/metrics").text
    families = {f.name: f for f in text_string_to_metric_families(body)}
    return {s.labels["rule"]: s.value for s in families["monitoring_alert_level"].samples}


def test_metrics_expose_each_enabled_rules_level_for_grafana(client, published):
    """P88-S7 (ADR 0252): the evaluated level as `monitoring_alert_level`."""
    client.post("/alert-rules", json={**RULE, "error_repeat": 1}, headers=ADMIN)
    off = client.post(
        "/alert-rules", json={**RULE, "name": "Aus", "enabled": False}, headers=ADMIN
    ).json()
    assert _alert_levels(client) == {"Speicher voll": -1}  # not yet evaluated, disabled absent
    tick(client, 85)
    assert _alert_levels(client) == {"Speicher voll": 1}
    tick(client, 99)
    assert _alert_levels(client) == {"Speicher voll": 2}
    tick(client, 10)
    assert _alert_levels(client) == {"Speicher voll": 0}
    sample = next(
        s
        for f in text_string_to_metric_families(client.get("/metrics").text)
        if f.name == "monitoring_alert_level"
        for s in f.samples
    )
    assert sample.labels == {"rule": "Speicher voll", "sensor": "storage.target.used_percent"}
    assert off["enabled"] is False
