import pytest
from fastapi.testclient import TestClient
from notification_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "notification-service"


def test_create_in_app_notification_for_unknown_recipient_returns_400(client):
    response = client.post(
        "/notifications",
        json={"channel": "in_app", "recipient": "dept-head", "subject": "S", "body": "B"},
    )
    assert response.status_code == 400


def test_create_in_app_notification_returns_sent(client, real_recipient):
    username, _email = real_recipient
    response = client.post(
        "/notifications",
        json={"channel": "in_app", "recipient": username, "subject": "S", "body": "B"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "sent"
    assert body["channel"] == "in_app"


def test_create_email_notification_for_unknown_recipient_returns_400(client):
    response = client.post(
        "/notifications",
        json={
            "channel": "email",
            "recipient": "does-not-exist@example.com",
            "subject": "S",
            "body": "B",
        },
    )
    assert response.status_code == 400


def test_create_email_notification_for_real_recipient_is_accepted(client, real_recipient):
    _username, email = real_recipient
    response = client.post(
        "/notifications",
        json={"channel": "email", "recipient": email, "subject": "S", "body": "B"},
    )
    assert response.status_code == 201


def test_create_webhook_notification_records_failure_when_unreachable(client):
    response = client.post(
        "/notifications",
        json={
            "channel": "webhook",
            "recipient": "http://127.0.0.1:1/nope",
            "subject": "S",
            "body": "B",
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "failed"


def test_get_unknown_notification_returns_404(client):
    response = client.get("/notifications/999999")
    assert response.status_code == 404


def test_list_notifications_filters_by_recipient(client, real_recipient):
    username, _email = real_recipient
    client.post(
        "/notifications",
        json={"channel": "in_app", "recipient": username, "subject": "S1", "body": "B"},
    )
    response = client.get("/notifications", params={"recipient": username})
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["subject"] == "S1"
