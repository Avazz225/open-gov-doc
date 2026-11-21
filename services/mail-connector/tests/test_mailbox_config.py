"""Mailbox administration (P88-S4a, ADR 0244): the table replaces
DMS_MAILBOXES, passwords are encrypted and write-only, a mailbox with
messages can only be disabled, and changes reach the poll loop without a
restart."""

import os

import httpx
import pytest
from fastapi.testclient import TestClient
from mail_connector import crypto
from mail_connector.backends.interface import RawIncomingMessage
from mail_connector.main import _ingest_message, app
from sqlalchemy import text

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
MAIL_ADMIN = {"X-DMS-Principal": "mail-connector-test-mail-admin"}
NOT_ADMIN = {"X-DMS-Principal": "mail-connector-test-nobody"}


@pytest.fixture(scope="session", autouse=True)
def _grant_mail_admin():
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        role_id = next(r["id"] for r in pc.get("/roles").json() if r["name"] == "domain-admin-mail")
        existing = pc.get(
            "/role-assignments", params={"principal_id": MAIL_ADMIN["X-DMS-Principal"]}
        ).json()
        if not any(a["role_id"] == role_id for a in existing):
            pc.post(
                "/role-assignments",
                json={
                    "principal_type": "user",
                    "principal_id": MAIL_ADMIN["X-DMS-Principal"],
                    "role_id": role_id,
                    "resource_id": "root",
                },
            ).raise_for_status()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _mailbox(**overrides) -> dict:
    return {
        "id": "finanzen",
        "name": "Poststelle Finanzen",
        "kind": "central",
        "inbound_protocol": "imap",
        "host": "imap.example.test",
        "username": "finanzen",
        "password": "geheim-123",
        **overrides,
    }


def test_the_default_mailbox_is_adopted_once_on_start(client):
    listed = client.get("/mailbox-config", headers=MAIL_ADMIN).json()
    central = next(m for m in listed if m["id"] == "central")
    assert central["has_password"] is True
    assert central["updated_by"] == "system:adopted-from-env"
    assert "password" not in central and "password_encrypted" not in central


async def test_adoption_does_not_repeat_after_every_mailbox_was_deleted(client, session):
    assert client.delete("/mailbox-config/central", headers=MAIL_ADMIN).status_code == 204
    # A second app start: the adoption marker keeps the env list out.
    with TestClient(app) as again:
        assert again.get("/mailbox-config", headers=MAIL_ADMIN).json() == []


def test_admin_api_requires_admin_mail_config(client):
    assert client.get("/mailbox-config").status_code == 401
    assert client.get("/mailbox-config", headers=NOT_ADMIN).status_code == 403
    assert client.post("/mailbox-config", json=_mailbox(), headers=NOT_ADMIN).status_code == 403


async def test_password_is_encrypted_at_rest_and_write_only(client, session):
    created = client.post("/mailbox-config", json=_mailbox(), headers=MAIL_ADMIN)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["has_password"] is True
    assert body["port"] == 993  # imap + TLS default
    stored = (
        await session.execute(
            text("SELECT password_encrypted FROM mail_connector.mailbox WHERE id = 'finanzen'")
        )
    ).scalar_one()
    assert b"geheim-123" not in stored
    key = crypto.load_key(os.environ["DMS_MAILBOX_CREDENTIALS_KEY"])
    assert crypto.decrypt(stored, key) == "geheim-123"

    # An update without a password keeps the stored one.
    update = _mailbox(name="Finanzen", password=None)
    assert (
        client.put("/mailbox-config/finanzen", json=update, headers=MAIL_ADMIN).status_code == 200
    )
    again = (
        await session.execute(
            text("SELECT password_encrypted FROM mail_connector.mailbox WHERE id = 'finanzen'")
        )
    ).scalar_one()
    assert crypto.decrypt(again, key) == "geheim-123"


def test_invalid_and_conflicting_configs_are_refused(client):
    assert client.post("/mailbox-config", json=_mailbox(), headers=MAIL_ADMIN).status_code == 201
    assert client.post("/mailbox-config", json=_mailbox(), headers=MAIL_ADMIN).status_code == 409
    departmental = _mailbox(id="abteilung", kind="departmental")
    response = client.post("/mailbox-config", json=departmental, headers=MAIL_ADMIN)
    assert response.status_code == 422
    assert "owning_group_id" in response.json()["detail"]
    no_password = _mailbox(id="ohne", password=None)
    assert client.post("/mailbox-config", json=no_password, headers=MAIL_ADMIN).status_code == 422
    renamed = client.put("/mailbox-config/finanzen", json=_mailbox(id="anders"), headers=MAIL_ADMIN)
    assert renamed.status_code == 422
    assert (
        client.post("/mailbox-config", json=_mailbox(id="Gross"), headers=MAIL_ADMIN).status_code
        == 422
    )


async def test_a_mailbox_with_messages_can_only_be_disabled(client, session):
    client.post("/mailbox-config", json=_mailbox(), headers=MAIL_ADMIN)
    await _ingest_message(
        session,
        "finanzen",
        RawIncomingMessage(uid="uid-1", raw_bytes=b"From: a@b\r\nSubject: x\r\n\r\nHallo"),
    )
    await session.commit()

    refused = client.delete("/mailbox-config/finanzen", headers=MAIL_ADMIN)
    assert refused.status_code == 409
    disabled = client.put(
        "/mailbox-config/finanzen", json=_mailbox(enabled=False, password=None), headers=MAIL_ADMIN
    )
    assert disabled.json()["enabled"] is False
    assert disabled.json()["message_count"] == 1
    # Still a name for the mail room's selector.
    listed = client.get(
        "/mailboxes", headers={"X-DMS-Principal": "p", "X-DMS-Roles": "dms-poststelle"}
    ).json()
    assert {"id": "finanzen", "enabled": False}.items() <= next(
        m for m in listed if m["id"] == "finanzen"
    ).items()


class _FakeBackend:
    def __init__(self, config):
        self.config = config

    async def check(self):
        if self.config.imap_password != "richtig":
            raise RuntimeError("LOGIN failed")

    async def fetch_new_messages(self):
        return []


def test_connection_test_uses_the_draft_or_the_stored_password(client):
    app.state.backend_factory = _FakeBackend
    client.post("/mailbox-config", json=_mailbox(password="richtig"), headers=MAIL_ADMIN)

    stored = client.post(
        "/mailbox-config/test",
        json=_mailbox(password=None, host="neu.example.test"),
        headers=MAIL_ADMIN,
    )
    assert stored.json() == {"ok": True, "error": None}
    wrong = client.post(
        "/mailbox-config/test", json=_mailbox(password="falsch"), headers=MAIL_ADMIN
    )
    assert wrong.json()["ok"] is False
    assert "LOGIN failed" in wrong.json()["error"]


def test_the_poll_loop_reads_the_table_and_records_its_result(client):
    """A disabled mailbox isn't polled; an enabled one is, through the
    factory, and its result shows up in the admin list. One tick runs on
    the app's own event loop (the client's portal)."""
    from mail_connector import main

    polled = []

    class Recording(_FakeBackend):
        async def fetch_new_messages(self):
            polled.append(self.config.id)
            raise RuntimeError("unreachable")

    app.state.backend_factory = Recording
    client.post("/mailbox-config", json=_mailbox(), headers=MAIL_ADMIN)
    client.post("/mailbox-config", json=_mailbox(id="aus", enabled=False), headers=MAIL_ADMIN)

    client.portal.call(main._poll_tick, app.state.session_factory)

    assert "finanzen" in polled and "aus" not in polled
    entry = next(
        m for m in client.get("/mailbox-config", headers=MAIL_ADMIN).json() if m["id"] == "finanzen"
    )
    assert entry["last_poll_ok"] is False
    assert "unreachable" in entry["last_poll_error"]
