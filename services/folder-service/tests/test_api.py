from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from folder_service.main import app


@pytest.fixture
def client():
    """`app.state.document_client` zeigt nach dem Lifespan-Start real auf
    `document-service` - für API-Tests hier durch einen Fake ersetzt, damit
    diese Tests unabhängig davon laufen, ob/wie `document-service` gerade
    deployed ist (die reine Kaskaden-LOGIK wird bereits in
    test_repository.py/test_retention.py gegen einen Fake geprüft; das echte
    Zusammenspiel verifiziert der Live-Docker-Smoke-Test, siehe PROGRESS.md)."""
    with TestClient(app) as c:
        fake_document_client = AsyncMock()
        fake_document_client.cascade_trash.return_value = []
        fake_document_client.cascade_restore.return_value = []
        fake_document_client.count_active.return_value = 0
        app.state.document_client = fake_document_client
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "folder-service"


def test_root_folder_exists(client):
    response = client.get("/folders/root")
    assert response.status_code == 200
    assert response.json()["parent_id"] is None


def test_create_folder_defaults_to_root_parent(client):
    response = client.post("/folders", json={"name": "Projekte", "created_by": "alice"})
    assert response.status_code == 201
    assert response.json()["parent_id"] == "root"


def test_create_folder_unknown_parent_returns_404(client):
    response = client.post(
        "/folders", json={"name": "X", "parent_id": "nope", "created_by": "alice"}
    )
    assert response.status_code == 404


def test_list_children(client):
    created = client.post("/folders", json={"name": "Projekte", "created_by": "alice"}).json()

    response = client.get("/folders/root/children")
    assert response.status_code == 200
    assert created["id"] in {f["id"] for f in response.json()}


def test_update_rename(client):
    created = client.post("/folders", json={"name": "Alt", "created_by": "alice"}).json()

    response = client.patch(f"/folders/{created['id']}", json={"name": "Neu"})
    assert response.status_code == 200
    assert response.json()["name"] == "Neu"


def test_move_to_self_returns_400(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()

    response = client.patch(f"/folders/{created['id']}", json={"parent_id": created["id"]})
    assert response.status_code == 400


def test_delete_non_empty_folder_returns_409(client):
    parent = client.post("/folders", json={"name": "Parent", "created_by": "alice"}).json()
    client.post(
        "/folders", json={"name": "Child", "parent_id": parent["id"], "created_by": "alice"}
    )

    response = client.delete(f"/folders/{parent['id']}")
    assert response.status_code == 409


def test_delete_empty_folder(client):
    created = client.post("/folders", json={"name": "Leer", "created_by": "alice"}).json()

    response = client.delete(f"/folders/{created['id']}")
    assert response.status_code == 204
    assert client.get(f"/folders/{created['id']}").status_code == 404


def test_trash_and_restore_folder_calls_document_cascade(client):
    """Verifiziert die Endpunkt-Verdrahtung zum Kaskaden-Aufruf an
    document-service (5.2, seit P7-S1b) - die reine Kaskaden-Logik
    (deleted_via_folder_id-Filterung etc.) prüft test_retention.py bereits
    direkt gegen das Repository."""
    parent = client.post("/folders", json={"name": "Akte", "created_by": "alice"}).json()

    trash_response = client.post(f"/folders/{parent['id']}/trash", json={"deleted_by": "alice"})
    assert trash_response.status_code == 200
    assert trash_response.json()["deleted_at"] is not None
    assert client.get(f"/folders/{parent['id']}").status_code == 404
    app.state.document_client.cascade_trash.assert_awaited_once_with(
        [parent["id"]], via_folder_id=parent["id"], deleted_by="alice"
    )

    restore_response = client.post(f"/folders/{parent['id']}/restore")
    assert restore_response.status_code == 200
    assert restore_response.json()["deleted_at"] is None
    app.state.document_client.cascade_restore.assert_awaited_once_with(parent["id"])


def test_restore_folder_not_deleted_returns_409(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()
    response = client.post(f"/folders/{created['id']}/restore")
    assert response.status_code == 409


def test_restore_unknown_folder_returns_404(client):
    response = client.post("/folders/does-not-exist/restore")
    assert response.status_code == 404


def test_list_deleted_folders_shows_only_trash(client):
    kept = client.post("/folders", json={"name": "Bleibt", "created_by": "alice"}).json()
    deleted = client.post("/folders", json={"name": "Weg", "created_by": "alice"}).json()
    client.post(f"/folders/{deleted['id']}/trash", json={"deleted_by": "alice"})

    response = client.get("/folders/deleted", params={"parent_id": "root"})

    assert response.status_code == 200
    ids = [f["id"] for f in response.json()]
    assert deleted["id"] in ids
    assert kept["id"] not in ids


def test_put_retention_sets_fields(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()

    response = client.put(
        f"/folders/{created['id']}/retention",
        json={"retention_until": "2030-01-01T00:00:00Z", "full_deletion": False, "reason": None},
    )

    assert response.status_code == 200
    assert response.json()["retention_until"] is not None


def test_put_retention_requires_reason_when_configured(client):
    client.put(
        "/retention-config", json={"deletion_reason_required": True, "reminder_lead_days": None}
    )
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()

    response = client.put(
        f"/folders/{created['id']}/retention",
        json={"retention_until": "2030-01-01T00:00:00Z", "full_deletion": True, "reason": None},
    )

    assert response.status_code == 422
    client.put(
        "/retention-config", json={"deletion_reason_required": False, "reminder_lead_days": None}
    )


def test_put_retention_unknown_folder_returns_404(client):
    response = client.put(
        "/folders/does-not-exist/retention",
        json={"retention_until": None, "full_deletion": False, "reason": None},
    )
    assert response.status_code == 404


def test_legal_hold_lifecycle(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()

    create_response = client.post(
        "/legal-holds",
        json={"folder_id": created["id"], "set_by": "alice", "reason": "Rechtsstreit"},
    )
    assert create_response.status_code == 201
    hold_id = create_response.json()["id"]

    list_response = client.get(
        "/legal-holds", params={"folder_id": created["id"], "active_only": True}
    )
    assert len(list_response.json()) == 1

    release_response = client.post(f"/legal-holds/{hold_id}/release", json={"released_by": "bob"})
    assert release_response.status_code == 200
    assert release_response.json()["released_at"] is not None


def test_release_legal_hold_twice_returns_409(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()
    hold_id = client.post(
        "/legal-holds", json={"folder_id": created["id"], "set_by": "alice", "reason": None}
    ).json()["id"]
    client.post(f"/legal-holds/{hold_id}/release", json={"released_by": "bob"})

    response = client.post(f"/legal-holds/{hold_id}/release", json={"released_by": "bob"})
    assert response.status_code == 409


def test_create_legal_hold_unknown_folder_returns_404(client):
    response = client.post(
        "/legal-holds", json={"folder_id": "does-not-exist", "set_by": "alice", "reason": None}
    )
    assert response.status_code == 404


def test_deletion_register_lists_entries(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()
    client.put(
        f"/folders/{created['id']}/retention",
        json={"retention_until": None, "full_deletion": True, "reason": "Aufgeräumt"},
    )
    # Direkt terminiert, aber noch nicht fällig - Löschregister bleibt leer.
    response = client.get("/deletion-register", params={"folder_id": created["id"]})
    assert response.status_code == 200
    assert response.json() == []


def test_retention_config_get_and_put(client):
    response = client.put(
        "/retention-config", json={"deletion_reason_required": True, "reminder_lead_days": 5}
    )
    assert response.status_code == 200
    assert response.json()["reminder_lead_days"] == 5

    get_response = client.get("/retention-config")
    assert get_response.json()["deletion_reason_required"] is True
    client.put(
        "/retention-config", json={"deletion_reason_required": False, "reminder_lead_days": None}
    )


def test_trash_config_get_and_put(client):
    response = client.put("/trash-config", json={"restore_period_days": 10})
    assert response.status_code == 200
    assert response.json()["restore_period_days"] == 10
    client.put("/trash-config", json={"restore_period_days": 30})
