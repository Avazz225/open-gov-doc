import os

import httpx
import pytest
from fastapi.testclient import TestClient
from notification_service.main import app, settings

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
NOTIFICATION_TEST_PRINCIPAL_ID = "notification-service-tests"
# Post-Roadmap Phase 19 Session 6 (ADR 0071): `PUT /roles/{id}` requires
# `admin.user_management` - separate test principal for `everyone_role_without`
# below (see `_grant_role_admin_permission`).
ROLE_ADMIN_PRINCIPAL_ID = "notification-service-test-role-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_role_admin_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-users")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": ROLE_ADMIN_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": ROLE_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(scope="session", autouse=True)
async def _grant_notification_config_permission():
    """Post-Roadmap Phase 38 Session 3: `PUT /email-templates/{use_case}`/
    `.../by-domain/{domain}`/`DELETE /email-templates/{id}` require
    `admin.notification_config` - unlike `notification.write` below, this
    is an EXISTING seeded domain-admin role (`domain-admin-notification`),
    granted directly to the default `client` fixture's own test principal."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-notification")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": NOTIFICATION_TEST_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": NOTIFICATION_TEST_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(scope="session", autouse=True)
async def _grant_notification_read_permission():
    """Phase 59 Session 1: `GET /notifications`/`GET /notifications/{id}`
    require `admin.notification_read` - a third, separate domain-admin
    role from `_grant_notification_config_permission` above, granted
    directly to the default `client` fixture's own test principal, same
    pattern."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-notification-read")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": NOTIFICATION_TEST_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": NOTIFICATION_TEST_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(scope="session", autouse=True)
async def _grant_notification_write_permission():
    """`notification.write` is deliberately NOT part of the "everyone"
    group (Post-Roadmap Phase 38 Session 2, see `main.py`
    `_require_notification_permission`) - unlike audit.read/virus_scan.*,
    this permission needs an explicit, idempotent, fixed-name throwaway
    role grant for the test principal, same pattern as reporting-service's
    `_grant_document_read_permission`."""
    role_name = "notification-service-test-write"
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role = next((r for r in roles if r["name"] == role_name), None)
        if role is None:
            created = await pc.post(
                "/roles",
                json={"name": role_name, "permissions": ["notification.write"]},
                headers={"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID},
            )
            created.raise_for_status()
            role = created.json()["role"]
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": NOTIFICATION_TEST_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role["id"] for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": NOTIFICATION_TEST_PRINCIPAL_ID,
                "role_id": role["id"],
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture
def client():
    """`permission_client` stays UNMOCKED (a real call against the running
    permission-service) - the `TestClient` therefore carries a
    `X-DMS-Principal` header by default (RBAC since Post-Roadmap Phase 38
    Session 2). Individual tests can override the header via
    `headers={"X-DMS-Principal": ""}` to exercise the negative case, same
    pattern as reporting-service/archival-service."""
    with TestClient(app, headers={"X-DMS-Principal": NOTIFICATION_TEST_PRINCIPAL_ID}) as c:
        yield c


@pytest.fixture
def everyone_role_without():
    """Not applicable to `notification.write` (deliberately not part of
    "everyone"), but reused by `test_create_notification_without_
    notification_write_permission_is_403` to remove the throwaway test
    role's own grant instead. Kept as a fixture for structural parity with
    the other newly-gated services."""
    role_management_headers = {"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID}
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        assignments = pc.get(
            "/role-assignments", params={"principal_id": NOTIFICATION_TEST_PRINCIPAL_ID}
        ).json()
        role_name = "notification-service-test-write"
        role = next(r for r in roles if r["name"] == role_name)
        assignment = next(a for a in assignments if a["role_id"] == role["id"])

        pc.delete(
            f"/role-assignments/{assignment['id']}", headers=role_management_headers
        ).raise_for_status()

        yield

        pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": NOTIFICATION_TEST_PRINCIPAL_ID,
                "role_id": role["id"],
                "resource_id": "root",
            },
            headers=role_management_headers,
        ).raise_for_status()


@pytest.fixture
def notification_read_role_removed():
    """Phase 59 Session 1: temporarily removes the default `client`
    fixture's `domain-admin-notification-read` grant to exercise the new
    `GET /notifications`/`GET /notifications/{id}` 403 path, same
    remove-then-restore pattern as `everyone_role_without` above."""
    role_management_headers = {"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID}
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        assignments = pc.get(
            "/role-assignments", params={"principal_id": NOTIFICATION_TEST_PRINCIPAL_ID}
        ).json()
        role = next(r for r in roles if r["name"] == "domain-admin-notification-read")
        assignment = next(a for a in assignments if a["role_id"] == role["id"])

        pc.delete(
            f"/role-assignments/{assignment['id']}", headers=role_management_headers
        ).raise_for_status()

        yield

        pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": NOTIFICATION_TEST_PRINCIPAL_ID,
                "role_id": role["id"],
                "resource_id": "root",
            },
            headers=role_management_headers,
        ).raise_for_status()


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
    body = response.json()
    assert body["status"] == "failed"
    assert body["attempts"] == 1
    assert body["next_retry_at"] is not None


def test_retry_returns_404_for_unknown_notification(client):
    response = client.post("/notifications/999999/retry")
    assert response.status_code == 404


def test_retry_returns_409_for_still_retryable_notification(client):
    created = client.post(
        "/notifications",
        json={
            "channel": "webhook",
            "recipient": "http://127.0.0.1:1/nope",
            "subject": "S",
            "body": "B",
        },
    ).json()

    response = client.post(f"/notifications/{created['id']}/retry")

    assert response.status_code == 409


def test_retry_reattempts_a_failed_permanent_notification(client):
    """Post-Roadmap Phase 20 Session 3 (ADR 0079): der Endpunkt unternimmt
    sofort einen neuen Zustellversuch statt nur zurueckzusetzen."""
    original_max_attempts = settings.max_notification_attempts
    settings.max_notification_attempts = 1
    try:
        created = client.post(
            "/notifications",
            json={
                "channel": "webhook",
                "recipient": "http://127.0.0.1:1/nope",
                "subject": "S",
                "body": "B",
            },
        ).json()
        assert created["status"] == "failed_permanent"

        response = client.post(f"/notifications/{created['id']}/retry")

        assert response.status_code == 200
        body = response.json()
        # Ziel bleibt unerreichbar - erneut sofort failed_permanent, attempts blieb bei 1
        # (zurueckgesetzt auf 0, dann durch den erneuten Fehlschlag wieder auf 1 erhoeht).
        assert body["status"] == "failed_permanent"
        assert body["attempts"] == 1
    finally:
        settings.max_notification_attempts = original_max_attempts


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


def test_list_notifications_without_principal_header_is_401(client):
    response = client.get("/notifications", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_list_notifications_without_read_permission_is_403(client, notification_read_role_removed):
    response = client.get("/notifications")
    assert response.status_code == 403


def test_get_notification_without_principal_header_is_401(client):
    response = client.get("/notifications/1", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_get_notification_without_read_permission_is_403(
    client, real_recipient, notification_read_role_removed
):
    username, _email = real_recipient
    created = client.post(
        "/notifications",
        json={"channel": "in_app", "recipient": username, "subject": "S", "body": "B"},
    )
    assert created.status_code == 201
    response = client.get(f"/notifications/{created.json()['id']}")
    assert response.status_code == 403


def test_create_notification_without_principal_header_is_401(client, real_recipient):
    username, _email = real_recipient
    response = client.post(
        "/notifications",
        json={"channel": "in_app", "recipient": username, "subject": "S", "body": "B"},
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_create_notification_without_notification_write_permission_is_403(
    client, real_recipient, everyone_role_without
):
    username, _email = real_recipient
    response = client.post(
        "/notifications",
        json={"channel": "in_app", "recipient": username, "subject": "S", "body": "B"},
    )
    assert response.status_code == 403


def test_create_notification_rate_limited_per_recipient(client, real_recipient):
    """Regression test for the per-recipient rate limit added alongside
    the RBAC fix (Post-Roadmap Phase 38 Session 2)."""
    username, _email = real_recipient
    original_max = settings.notification_rate_limit_max_per_recipient
    settings.notification_rate_limit_max_per_recipient = 2
    try:
        for _ in range(2):
            response = client.post(
                "/notifications",
                json={"channel": "in_app", "recipient": username, "subject": "S", "body": "B"},
            )
            assert response.status_code == 201

        response = client.post(
            "/notifications",
            json={"channel": "in_app", "recipient": username, "subject": "S", "body": "B"},
        )
        assert response.status_code == 429
    finally:
        settings.notification_rate_limit_max_per_recipient = original_max


def test_put_email_template_default_persists(client):
    response = client.put(
        "/email-templates/workflow.task.escalated",
        json={"subject_template": "Test-Betreff", "body_template": "Test-Text {task_name}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["use_case"] == "workflow.task.escalated"
    assert body["recipient_domain_pattern"] is None
    assert body["subject_template"] == "Test-Betreff"


def test_put_email_template_default_without_principal_header_is_401(client):
    response = client.put(
        "/email-templates/workflow.task.escalated",
        json={"subject_template": "x", "body_template": "y"},
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_put_email_template_default_without_notification_config_permission_is_403(client):
    response = client.put(
        "/email-templates/workflow.task.escalated",
        json={"subject_template": "x", "body_template": "y"},
        headers={"X-DMS-Principal": "some-random-authenticated-caller"},
    )
    assert response.status_code == 403


def test_put_email_template_for_domain_without_permission_is_403(client):
    response = client.put(
        "/email-templates/workflow.task.escalated/by-domain/example.com",
        json={"subject_template": "x", "body_template": "y"},
        headers={"X-DMS-Principal": "some-random-authenticated-caller"},
    )
    assert response.status_code == 403


def test_delete_email_template_without_permission_is_403(client):
    created = client.put(
        "/email-templates/workflow.task.escalated",
        json={"subject_template": "x", "body_template": "y"},
    ).json()
    response = client.delete(
        f"/email-templates/{created['id']}",
        headers={"X-DMS-Principal": "some-random-authenticated-caller"},
    )
    assert response.status_code == 403
