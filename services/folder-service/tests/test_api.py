import os
from unittest.mock import AsyncMock

import httpx
import pytest
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from folder_service.main import app

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


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


def test_inbox_and_outbox_folders_exist_under_root(client):
    inbox = client.get("/folders/inbox")
    outbox = client.get("/folders/outbox")
    assert inbox.status_code == 200
    assert inbox.json()["parent_id"] == "root"
    assert inbox.json()["name"] == "Posteingang"
    assert outbox.status_code == 200
    assert outbox.json()["parent_id"] == "root"
    assert outbox.json()["name"] == "Postausgang"


def test_inbox_folder_cannot_be_renamed(client):
    response = client.patch("/folders/inbox", json={"name": "Umbenannt"})
    assert response.status_code == 409


def test_inbox_folder_cannot_be_moved(client):
    new_parent = client.post("/folders", json={"name": "Ziel", "created_by": "alice"}).json()
    response = client.patch("/folders/inbox", json={"parent_id": new_parent["id"]})
    assert response.status_code == 409


def test_inbox_folder_cannot_be_hard_deleted(client):
    response = client.delete("/folders/inbox")
    assert response.status_code == 409


def test_outbox_folder_cannot_be_trashed(client):
    response = client.post("/folders/outbox/trash", json={"deleted_by": "alice"})
    assert response.status_code == 409


def test_attribute_only_patch_on_inbox_still_allowed(client):
    """Der Schutz gilt gezielt Name/Elternteil (2.5) - eine reine
    Attribut-Änderung (kein `name`/`parent_id` im Payload) bleibt möglich."""
    response = client.patch("/folders/inbox", json={"attributes": {"note": "x"}})
    assert response.status_code == 200


def test_create_folder_defaults_to_root_parent(client):
    response = client.post("/folders", json={"name": "Projekte", "created_by": "alice"})
    assert response.status_code == 201
    assert response.json()["parent_id"] == "root"


def test_create_folder_publishes_event_with_actor(client, monkeypatch):
    """First-class Actor-Feld (5.4b-Voraussetzung, seit P7-S2) - der
    publizierte `folder.resource.created`-Event trägt den Ersteller als
    `actor`, nicht nur ad-hoc unter `payload["created_by"]`."""
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    response = client.post("/folders", json={"name": "Projekte", "created_by": "alice"})
    assert response.status_code == 201

    created_events = [e for e in published if e.event_type == "folder.resource.created"]
    assert len(created_events) == 1
    assert created_events[0].actor == "alice"


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
    trash_result = trash_response.json()
    assert trash_result["status"] == "trashed"
    assert trash_result["folder"]["deleted_at"] is not None
    assert trash_result["approval_request_id"] is None
    assert client.get(f"/folders/{parent['id']}").status_code == 404
    app.state.document_client.cascade_trash.assert_awaited_once_with(
        [parent["id"]], via_folder_id=parent["id"], deleted_by="alice"
    )

    restore_response = client.post(f"/folders/{parent['id']}/restore")
    assert restore_response.status_code == 200
    assert restore_response.json()["deleted_at"] is None
    app.state.document_client.cascade_restore.assert_awaited_once_with(parent["id"])


def test_trash_folder_with_approval_required_defers_execution(client):
    """Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c) - echte
    Integration gegen den lokal laufenden permission-service, gleiches
    Muster wie document-service's analoger Test."""
    httpx.put(
        f"{PERMISSION_SERVICE_URL}/approval-config/folder.delete",
        json={"requires_approval": True},
    )
    try:
        created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()

        response = client.post(f"/folders/{created['id']}/trash", json={"deleted_by": "alice"})

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "pending_approval"
        assert result["approval_request_id"] is not None
        assert result["folder"] is None

        # Ordner ist weiterhin nicht gelöscht.
        assert client.get(f"/folders/{created['id']}").status_code == 200
    finally:
        httpx.put(
            f"{PERMISSION_SERVICE_URL}/approval-config/folder.delete",
            json={"requires_approval": False},
        )


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


def test_list_deleted_folders_without_scope_requires_parent_id(client):
    response = client.get("/folders/deleted")
    assert response.status_code == 422


def test_trash_folder_persists_deleted_by(client):
    """P15-S0-Fund: `deleted_by` wurde bislang entgegengenommen, aber nie
    tatsächlich gespeichert - Voraussetzung für den persönlichen Papierkorb
    (2.5, P15-S1)."""
    created = client.post("/folders", json={"name": "Weg", "created_by": "alice"}).json()
    client.post(f"/folders/{created['id']}/trash", json={"deleted_by": "alice"})

    response = client.get(
        "/folders/deleted", params={"scope": "personal"}, headers={"X-DMS-Principal": "alice"}
    )
    assert response.status_code == 200
    body = response.json()
    assert created["id"] in [f["id"] for f in body]
    assert next(f for f in body if f["id"] == created["id"])["deleted_by"] == "alice"


def test_list_deleted_folders_personal_scope_hides_other_users_items(client):
    own = client.post("/folders", json={"name": "Eigener", "created_by": "alice"}).json()
    other = client.post("/folders", json={"name": "Fremder", "created_by": "bob"}).json()
    client.post(f"/folders/{own['id']}/trash", json={"deleted_by": "alice"})
    client.post(f"/folders/{other['id']}/trash", json={"deleted_by": "bob"})

    response = client.get(
        "/folders/deleted", params={"scope": "personal"}, headers={"X-DMS-Principal": "alice"}
    )

    ids = [f["id"] for f in response.json()]
    assert own["id"] in ids
    assert other["id"] not in ids


def test_list_deleted_folders_personal_scope_without_principal_returns_401(client):
    response = client.get("/folders/deleted", params={"scope": "personal"})
    assert response.status_code == 401


def test_list_deleted_folders_admin_scope_requires_role(client):
    created = client.post("/folders", json={"name": "Weg", "created_by": "alice"}).json()
    client.post(f"/folders/{created['id']}/trash", json={"deleted_by": "alice"})

    response = client.get("/folders/deleted", params={"scope": "admin"})
    assert response.status_code == 403

    response = client.get(
        "/folders/deleted", params={"scope": "admin"}, headers={"X-DMS-Roles": "dms-admin"}
    )
    assert response.status_code == 200
    assert created["id"] in [f["id"] for f in response.json()]


def test_purge_folder_not_in_trash_returns_409(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()
    response = client.post(
        f"/folders/{created['id']}/purge",
        headers={"X-DMS-Principal": "admin", "X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 409


def test_purge_folder_unknown_returns_404(client):
    response = client.post(
        "/folders/does-not-exist/purge",
        headers={"X-DMS-Principal": "admin", "X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 404


def test_purge_folder_without_principal_returns_401(client):
    created = client.post("/folders", json={"name": "Weg", "created_by": "alice"}).json()
    client.post(f"/folders/{created['id']}/trash", json={"deleted_by": "alice"})
    response = client.post(f"/folders/{created['id']}/purge")
    assert response.status_code == 401


def test_purge_folder_without_admin_role_returns_403(client):
    created = client.post("/folders", json={"name": "Weg", "created_by": "alice"}).json()
    client.post(f"/folders/{created['id']}/trash", json={"deleted_by": "alice"})
    response = client.post(f"/folders/{created['id']}/purge", headers={"X-DMS-Principal": "alice"})
    assert response.status_code == 403


def test_purge_folder_with_admin_role_hard_deletes(client):
    created = client.post("/folders", json={"name": "Weg", "created_by": "alice"}).json()
    client.post(f"/folders/{created['id']}/trash", json={"deleted_by": "alice"})

    response = client.post(
        f"/folders/{created['id']}/purge",
        headers={"X-DMS-Principal": "admin", "X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 204

    still_there = client.get(
        "/folders/deleted", params={"scope": "admin"}, headers={"X-DMS-Roles": "dms-admin"}
    ).json()
    assert created["id"] not in [f["id"] for f in still_there]
    register = client.get("/deletion-register").json()
    entry = next(e for e in register if e["folder_id"] == created["id"])
    assert entry["trigger"] == "manual_purge"
    assert entry["triggered_by"] == "admin"


def test_purge_folder_with_remaining_child_row_returns_409(client):
    """Gleiche Sicherheitsprüfung wie die automatische Zwangslöschung
    (`_execute_or_defer_forced_deletion`/`has_any_child_folder_row`) - der
    Unterordner wird beim Trashen des Elternordners mitkaskadiert (bleibt
    aber als Zeile bestehen), ein physisches Entfernen des Elternordners
    würde sonst mit einer FK-Violation fehlschlagen."""
    parent = client.post("/folders", json={"name": "Eltern", "created_by": "alice"}).json()
    client.post("/folders", json={"name": "Kind", "parent_id": parent["id"], "created_by": "alice"})
    client.post(f"/folders/{parent['id']}/trash", json={"deleted_by": "alice"})

    response = client.post(
        f"/folders/{parent['id']}/purge",
        headers={"X-DMS-Principal": "admin", "X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 409


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


LEGAL_HOLD_ADMIN_HEADERS = {"X-DMS-Principal": "folder-service-test-legal-hold-admin"}


def test_create_legal_hold_without_permission_is_403(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()
    response = client.post(
        "/legal-holds",
        json={"folder_id": created["id"], "set_by": "alice", "reason": "Rechtsstreit"},
        headers={"X-DMS-Principal": "no-legal-hold-permission-user"},
    )
    assert response.status_code == 403


def test_legal_hold_lifecycle(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()

    create_response = client.post(
        "/legal-holds",
        json={"folder_id": created["id"], "set_by": "alice", "reason": "Rechtsstreit"},
        headers=LEGAL_HOLD_ADMIN_HEADERS,
    )
    assert create_response.status_code == 201
    hold_id = create_response.json()["id"]

    list_response = client.get(
        "/legal-holds", params={"folder_id": created["id"], "active_only": True}
    )
    assert len(list_response.json()) == 1

    release_response = client.post(
        f"/legal-holds/{hold_id}/release",
        json={"released_by": "bob"},
        headers=LEGAL_HOLD_ADMIN_HEADERS,
    )
    assert release_response.status_code == 200
    assert release_response.json()["released_at"] is not None


def test_release_legal_hold_twice_returns_409(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()
    hold_id = client.post(
        "/legal-holds",
        json={"folder_id": created["id"], "set_by": "alice", "reason": None},
        headers=LEGAL_HOLD_ADMIN_HEADERS,
    ).json()["id"]
    client.post(
        f"/legal-holds/{hold_id}/release",
        json={"released_by": "bob"},
        headers=LEGAL_HOLD_ADMIN_HEADERS,
    )

    response = client.post(
        f"/legal-holds/{hold_id}/release",
        json={"released_by": "bob"},
        headers=LEGAL_HOLD_ADMIN_HEADERS,
    )
    assert response.status_code == 409


def test_create_legal_hold_unknown_folder_returns_404(client):
    response = client.post(
        "/legal-holds",
        json={"folder_id": "does-not-exist", "set_by": "alice", "reason": None},
        headers=LEGAL_HOLD_ADMIN_HEADERS,
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


def test_reconcile_restore_deletion_requires_admin_role(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()
    response = client.post(
        f"/folders/{created['id']}/reconcile-restore-deletion",
        json={"original_entry_id": "led-1", "reason": None},
    )
    assert response.status_code == 403
    assert client.get(f"/folders/{created['id']}").status_code == 200


def test_reconcile_restore_deletion_performs_real_forced_deletion(client):
    """10.4/P11-S4: derselbe Mechanismus wie die ursprüngliche Zwangslöschung
    - Ordner ist danach wirklich weg, mit echtem DeletionRegisterEntry."""
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()

    response = client.post(
        f"/folders/{created['id']}/reconcile-restore-deletion",
        json={"original_entry_id": "led-42", "reason": "Restore-Abgleich"},
        headers={"X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 204
    assert client.get(f"/folders/{created['id']}").status_code == 404

    register = client.get("/deletion-register", params={"folder_id": created["id"]}).json()
    assert len(register) == 1
    assert register[0]["trigger"] == "forced_deletion"
    assert register[0]["triggered_by"] == "system:restore-reconciliation"


def test_reconcile_restore_deletion_unknown_folder_returns_404(client):
    response = client.post(
        "/folders/does-not-exist/reconcile-restore-deletion",
        json={"original_entry_id": "led-1", "reason": None},
        headers={"X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 404
    assert client.get("/deletion-register").json() == []


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


def _create_folder(client, *, name, parent_id="root", object_type_id=None):
    response = client.post(
        "/folders",
        json={
            "name": name,
            "parent_id": parent_id,
            "object_type_id": object_type_id,
            "attributes": {},
            "created_by": "alice",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_and_list_folder_template(client):
    projekte = _create_folder(client, name="Projekte")
    _create_folder(client, name="Vertraege", parent_id=projekte["id"])

    response = client.post(
        "/folder-templates",
        json={
            "source_folder_id": projekte["id"],
            "name": "Aktenplan-Rohbau",
            "description": "Standard-Struktur",
            "created_by": "alice",
        },
    )
    assert response.status_code == 201
    template = response.json()
    assert template["name"] == "Aktenplan-Rohbau"

    listed = client.get("/folder-templates").json()
    assert [t["id"] for t in listed] == [template["id"]]


def test_create_folder_template_unknown_source_returns_404(client):
    response = client.post(
        "/folder-templates",
        json={
            "source_folder_id": "does-not-exist",
            "name": "X",
            "description": None,
            "created_by": "alice",
        },
    )
    assert response.status_code == 404


def test_get_folder_template_returns_structure(client):
    projekte = _create_folder(client, name="Projekte")
    _create_folder(client, name="Vertraege", parent_id=projekte["id"])
    template = client.post(
        "/folder-templates",
        json={
            "source_folder_id": projekte["id"],
            "name": "Vorlage",
            "description": None,
            "created_by": "alice",
        },
    ).json()

    response = client.get(f"/folder-templates/{template['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["structure"]["name"] == "Projekte"
    assert body["structure"]["children"][0]["name"] == "Vertraege"
    assert body["structure"]["children"][0]["object_type_id"] is None


def test_get_folder_template_returns_404_for_unknown_id(client):
    response = client.get("/folder-templates/does-not-exist")
    assert response.status_code == 404


def test_delete_folder_template(client):
    projekte = _create_folder(client, name="Projekte")
    template = client.post(
        "/folder-templates",
        json={
            "source_folder_id": projekte["id"],
            "name": "Vorlage",
            "description": None,
            "created_by": "alice",
        },
    ).json()

    response = client.delete(f"/folder-templates/{template['id']}")

    assert response.status_code == 204
    assert client.get("/folder-templates").json() == []


def test_delete_folder_template_returns_404_for_unknown_id(client):
    response = client.delete("/folder-templates/does-not-exist")
    assert response.status_code == 404


def test_apply_folder_template_creates_folders_under_target(client):
    projekte = _create_folder(client, name="Projekte")
    _create_folder(client, name="Vertraege", parent_id=projekte["id"])
    template = client.post(
        "/folder-templates",
        json={
            "source_folder_id": projekte["id"],
            "name": "Vorlage",
            "description": None,
            "created_by": "alice",
        },
    ).json()
    target = _create_folder(client, name="Neues Projekt")

    response = client.post(
        f"/folder-templates/{template['id']}/apply",
        json={"target_parent_id": target["id"], "created_by": "bob"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert body["root_folder"]["name"] == "Projekte"
    assert body["root_folder"]["parent_id"] == target["id"]

    children = client.get(f"/folders/{body['root_folder']['id']}/children").json()
    assert [c["name"] for c in children] == ["Vertraege"]
    # Angewendete Ordner sind unabhängige Kopien - keine Attributwerte aus
    # dem Rohbau übernommen (es gab keine), aber auch keine Verknüpfung
    # zurück zur Vorlage.
    assert children[0]["attributes"] == {}


def test_apply_folder_template_unknown_template_returns_404(client):
    target = _create_folder(client, name="Ziel")
    response = client.post(
        "/folder-templates/does-not-exist/apply",
        json={"target_parent_id": target["id"], "created_by": "bob"},
    )
    assert response.status_code == 404


def test_apply_folder_template_unknown_target_returns_404(client):
    projekte = _create_folder(client, name="Projekte")
    template = client.post(
        "/folder-templates",
        json={
            "source_folder_id": projekte["id"],
            "name": "Vorlage",
            "description": None,
            "created_by": "alice",
        },
    ).json()

    response = client.post(
        f"/folder-templates/{template['id']}/apply",
        json={"target_parent_id": "does-not-exist", "created_by": "bob"},
    )

    assert response.status_code == 404
