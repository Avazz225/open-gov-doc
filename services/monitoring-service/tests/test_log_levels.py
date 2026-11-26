"""Central log levels (P97-S1, ADR 0267)."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from monitoring_service import log_levels
from monitoring_service.main import app, run_log_level_tick

ADMIN = {"x-dms-principal": "alice"}
CATALOG = {
    "monitoring-service": ["monitoring_service.main"],
    "workflow-service": ["workflow_service.repository", "sqlalchemy.engine"],
    "permission-service": ["permission_service.repository"],
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        app.state.auth_client = AsyncMock()
        app.state.auth_client.get_active_superuser.return_value = (False, None)
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        app.state.registry_client = AsyncMock()
        app.state.registry_client.list_active_instances.return_value = []
        app.state.registry_client.service_loggers.return_value = CATALOG
        yield c
    # Leave this test process's own loggers as they were.
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("monitoring_service.main").setLevel(logging.NOTSET)


def test_levels_are_stored_resolved_and_handed_out_with_the_sensor_config(client):
    assert client.put("/log-levels", json={"level": "WARNING"}, headers=ADMIN).status_code == 200
    client.put("/log-levels", json={"service": "workflow-service", "level": "error"}, headers=ADMIN)
    response = client.put(
        "/log-levels",
        json={
            "service": "workflow-service",
            "logger": "workflow_service.repository",
            "level": "DEBUG",
        },
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert {s["scope"] for s in body["settings"]} == {
        "global",
        "service:workflow-service",
        "logger:workflow-service:workflow_service.repository",
    }
    workflow = body["effective"]["workflow-service"]
    assert (workflow["root_level"], workflow["root_source"]) == ("ERROR", "service")
    assert workflow["loggers"]["workflow_service.repository"] == {
        "level": "DEBUG",
        "source": "service",
    }
    permission = body["effective"]["permission-service"]
    assert (permission["root_level"], permission["root_source"]) == ("WARNING", "global")
    assert body["catalog"] == CATALOG

    distributed = client.get("/sensor-config").json()["log_levels"]
    assert distributed == {
        "global": "WARNING",
        "services": {"workflow-service": "ERROR"},
        "loggers": [
            {
                "service": "workflow-service",
                "logger": "workflow_service.repository",
                "level": "DEBUG",
            }
        ],
    }


def test_debug_always_expires_and_is_capped(client):
    before = datetime.now(UTC)
    body = client.put(
        "/log-levels", json={"logger": "sqlalchemy.engine", "level": "DEBUG"}, headers=ADMIN
    ).json()
    [setting] = body["settings"]
    assert setting["scope"] == "logger:*:sqlalchemy.engine"
    expires = datetime.fromisoformat(setting["expires_at"])
    assert timedelta(minutes=59) < expires - before <= timedelta(minutes=61)

    too_long = client.put(
        "/log-levels",
        json={"logger": "sqlalchemy.engine", "level": "DEBUG", "expires_in_minutes": 2000},
        headers=ADMIN,
    )
    assert too_long.status_code == 422
    assert "höchstens" in too_long.json()["detail"]

    # Other levels stay until removed, unless a duration is given.
    info = client.put(
        "/log-levels", json={"service": "workflow-service", "level": "INFO"}, headers=ADMIN
    ).json()
    assert (
        next(s for s in info["settings"] if s["service"] == "workflow-service")["expires_at"]
        is None
    )


def test_expired_levels_drop_out_and_are_purged_once(client):
    client.put(
        "/log-levels",
        json={"service": "workflow-service", "level": "DEBUG", "expires_in_minutes": 1},
        headers=ADMIN,
    )
    assert client.get("/sensor-config").json()["log_levels"]["services"] == {
        "workflow-service": "DEBUG"
    }

    later = datetime.now(UTC) + timedelta(minutes=2)
    # Run in the app's own event loop (its engine is bound to it).
    expired = client.portal.call(lambda: run_log_level_tick(now=later))
    assert [e["scope"] for e in expired] == ["service:workflow-service"]
    # A second replica's tick finds nothing left to announce.
    assert client.portal.call(lambda: run_log_level_tick(now=later)) == []
    assert client.get("/sensor-config").json()["log_levels"]["services"] == {}


def test_reset_falls_back_and_unknown_is_404(client):
    client.put("/log-levels", json={"service": "workflow-service", "level": "ERROR"}, headers=ADMIN)
    response = client.delete("/log-levels", params={"service": "workflow-service"}, headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["effective"]["workflow-service"]["root_source"] == "env"
    again = client.delete("/log-levels", params={"service": "workflow-service"}, headers=ADMIN)
    assert again.status_code == 404


def test_validation_and_permission(client):
    cases = [
        ({"level": "LOUD"}, "Unbekannter Level"),
        ({"service": "workflw-service", "level": "INFO"}, "Unbekannter Dienst"),
        ({"service": "*", "level": "INFO"}, "nur zusammen mit einem Logger"),
        ({"logger": "bad name!", "level": "INFO"}, "Ungültiger Logger-Name"),
        (
            {"level": "INFO", "expires_in_minutes": 5, "expires_at": "2030-01-01T00:00:00Z"},
            "Nur eines",
        ),
    ]
    for payload, message in cases:
        response = client.put("/log-levels", json=payload, headers=ADMIN)
        assert response.status_code == 422, payload
        assert message in response.json()["detail"]

    app.state.permission_client.has_permission.return_value = False
    assert client.put("/log-levels", json={"level": "INFO"}, headers=ADMIN).status_code == 403
    assert client.delete("/log-levels", headers=ADMIN).status_code == 403


def test_without_a_registry_answer_the_service_name_isnt_checked(client):
    app.state.registry_client.service_loggers.side_effect = RuntimeError("down")
    response = client.put(
        "/log-levels", json={"service": "new-service", "level": "INFO"}, headers=ADMIN
    )
    assert response.status_code == 200
    assert response.json()["catalog"] == {}


def test_monitoring_service_applies_its_own_levels_right_away(client):
    client.put(
        "/log-levels",
        json={
            "service": "monitoring-service",
            "logger": "monitoring_service.main",
            "level": "ERROR",
        },
        headers=ADMIN,
    )
    assert logging.getLogger("monitoring_service.main").level == logging.ERROR


def test_scope_parsing_round_trips():
    for service, logger in (
        (None, None),
        ("workflow-service", None),
        ("workflow-service", "workflow_service.repository"),
        (None, "httpx"),
    ):
        scope = log_levels.make_scope(service, logger)
        assert log_levels.Scope.parse(scope.key) == scope
    assert log_levels.make_scope(None, "httpx").key == "logger:*:httpx"
