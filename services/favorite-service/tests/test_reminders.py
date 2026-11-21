"""Follow-up reminders (Wiedervorlagen, P90-S1, ADR 0246) through the API."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from favorite_service import main
from favorite_service.main import app

ALICE = {"X-DMS-Username": "alice"}
BOB = {"X-DMS-Username": "bob"}


@pytest.fixture
def published(monkeypatch):
    events = []

    async def record(event_type, subject, payload, actor=None):
        events.append((event_type, subject, payload, actor))

    monkeypatch.setattr(main, "publish_event", record)
    return events


@pytest.fixture
def client(published):
    with TestClient(app) as c:
        yield c


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def _create(client, *, delta=timedelta(days=7), note="Antwort prüfen", object_id="doc-1"):
    response = client.post(
        "/reminders",
        json={
            "user_id": "alice",
            "object_type": "document",
            "object_id": object_id,
            "due_at": _iso(delta),
            "note": note,
        },
        headers=ALICE,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _tick(client):
    return client.portal.call(main.run_reminder_tick)


def test_only_the_owner_creates_lists_and_changes_reminders(client):
    payload = {
        "user_id": "alice",
        "object_type": "case",
        "object_id": "c-1",
        "due_at": _iso(timedelta(days=1)),
    }
    assert client.post("/reminders", json=payload, headers=BOB).status_code == 403
    assert client.post("/reminders", json=payload).status_code == 401
    mine = _create(client)
    assert client.get("/reminders", params={"user_id": "alice"}, headers=BOB).status_code == 403
    # Someone else's reminder looks missing, whatever the action.
    for call in (
        lambda: client.patch(f"/reminders/{mine['id']}", json={"note": "x"}, headers=BOB),
        lambda: client.post(f"/reminders/{mine['id']}/done", headers=BOB),
        lambda: client.delete(f"/reminders/{mine['id']}", headers=BOB),
    ):
        assert call().status_code == 404
    assert client.get("/reminders", params={"user_id": "alice"}, headers=ALICE).json()[0][
        "note"
    ] == ("Antwort prüfen")


def test_a_due_time_needs_a_time_zone(client):
    payload = {
        "user_id": "alice",
        "object_type": "document",
        "object_id": "d",
        "due_at": "2026-12-01T09:00:00",
    }
    assert client.post("/reminders", json=payload, headers=ALICE).status_code == 422


def test_due_listing_count_and_one_notification_per_due_time(client, published):
    later = _create(client, delta=timedelta(days=3), note="später")
    due = _create(client, delta=timedelta(minutes=-5), note="jetzt fällig", object_id="doc-2")

    listed = client.get(
        "/reminders", params={"user_id": "alice", "due": "true"}, headers=ALICE
    ).json()
    assert [r["id"] for r in listed] == [due["id"]]
    assert listed[0]["is_due"] is True
    assert client.get(
        "/reminders/due-count", params={"user_id": "alice"}, headers=ALICE
    ).json() == {"due": 1}

    published.clear()
    assert _tick(client) == 1
    (event,) = [e for e in published if e[0] == "favorite.reminder.due"]
    assert event[1] == due["id"]
    assert event[2]["user_id"] == "alice" and event[2]["note"] == "jetzt fällig"
    assert event[3] == "system:favorite-reminders"
    # Notified once: the next tick has nothing, though it is still listed as due.
    assert _tick(client) == 0
    assert client.get(
        "/reminders/due-count", params={"user_id": "alice"}, headers=ALICE
    ).json() == {"due": 1}
    assert later["is_due"] is False


def test_snooze_rearms_the_notification(client, published):
    reminder = _create(client, delta=timedelta(minutes=-1))
    _tick(client)
    past = client.post(
        f"/reminders/{reminder['id']}/snooze",
        json={"until": _iso(timedelta(minutes=-1))},
        headers=ALICE,
    )
    assert past.status_code == 422

    snoozed = client.post(
        f"/reminders/{reminder['id']}/snooze",
        json={"until": _iso(timedelta(days=2))},
        headers=ALICE,
    ).json()
    assert (snoozed["is_due"], snoozed["notified_at"]) == (False, None)

    # Moved back into the past by editing: due and notified again.
    client.patch(
        f"/reminders/{reminder['id']}", json={"due_at": _iso(timedelta(seconds=-1))}, headers=ALICE
    )
    published.clear()
    assert _tick(client) == 1


def test_done_reopen_and_delete(client, published):
    reminder = _create(client, delta=timedelta(minutes=-1))
    done = client.post(f"/reminders/{reminder['id']}/done", headers=ALICE).json()
    assert (done["status"], done["is_due"]) == ("done", False)
    assert done["completed_at"] is not None
    assert client.post(f"/reminders/{reminder['id']}/done", headers=ALICE).status_code == 409
    assert (
        client.patch(f"/reminders/{reminder['id']}", json={"note": "x"}, headers=ALICE).status_code
        == 409
    )
    # A done reminder is never notified.
    assert _tick(client) == 0

    reopened = client.post(f"/reminders/{reminder['id']}/reopen", headers=ALICE).json()
    assert (reopened["status"], reopened["completed_at"]) == ("open", None)

    assert client.delete(f"/reminders/{reminder['id']}", headers=ALICE).status_code == 204
    assert client.get("/reminders", params={"user_id": "alice"}, headers=ALICE).json() == []
    assert [e[0] for e in published].count("favorite.reminder.deleted") == 1


def test_filters_by_object_and_status(client):
    _create(client, object_id="doc-1")
    _create(client, object_id="doc-1", delta=timedelta(days=30), note="Archivieren")
    other = _create(client, object_id="doc-2")
    client.post(f"/reminders/{other['id']}/done", headers=ALICE)

    on_doc_1 = client.get(
        "/reminders",
        params={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=ALICE,
    ).json()
    assert [r["note"] for r in on_doc_1] == ["Antwort prüfen", "Archivieren"]  # earliest first
    done = client.get(
        "/reminders", params={"user_id": "alice", "status": "done"}, headers=ALICE
    ).json()
    assert [r["id"] for r in done] == [other["id"]]
    assert (
        client.get(
            "/reminders", params={"user_id": "alice", "status": "x"}, headers=ALICE
        ).status_code
        == 422
    )
