import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from dms_eventbus_client import Event
from document_service.main import app
from fastapi.testclient import TestClient

STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")


def _grant_document_read(principal_id: str) -> None:
    """Vergibt `document.read` auf `root` für `principal_id` (4.2a) - gleiches
    Muster wie search-service's `_grant_root_read`. Dokumente, die über den
    `upload()`-Helfer ohne `folder_id` angelegt werden, prüfen intern gegen
    die Ressource `"root"` (siehe main.py's Freigabelink-Endpunkte)."""
    role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={
            "name": f"share-link-test-role-{uuid.uuid4().hex[:8]}",
            "permissions": ["document.read"],
        },
        timeout=30.0,
    )
    role.raise_for_status()
    assignment = httpx.post(
        f"{PERMISSION_SERVICE_URL}/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": principal_id,
            "role_id": role.json()["id"],
            "resource_id": "root",
        },
        timeout=30.0,
    )
    assignment.raise_for_status()


# Standardisierte EICAR-Testdatei-Signatur (https://www.eicar.org/) - von
# echten Antivirus-Produkten zu Integrationstestzwecken erkannt, hier zum
# Verifizieren der Virenscan-Gate-Integration (10.3, ADR 0010) verwendet.
EICAR_SIGNATURE = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$" "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
).encode("ascii")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def upload(client, *, content=b"Hallo Welt", title="Vertrag", created_by="alice", **extra):
    data = {"title": title, "created_by": created_by, **extra}
    files = {"file": ("vertrag.pdf", content, "application/pdf")}
    return client.post("/documents", data=data, files=files)


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "document-service"


def test_metrics_endpoint_exposes_own_pilot_sensors(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "document_upload_duration" in response.text
    assert "document_count_active_total" in response.text


def test_upload_sensor_records_duration_when_active(client, monkeypatch):
    monkeypatch.setattr(app.state.upload_duration_sensor, "_is_active", lambda name: True)
    upload(client)
    response = client.get("/metrics")
    assert "document_upload_duration_count 1.0" in response.text


def test_upload_sensor_skips_recording_when_inactive(client, monkeypatch):
    """Keine Erfassung bei Deaktivierung (10.1) - nicht nur unsichtbar: der
    Timer wird nie gestartet, `observe()` nie aufgerufen, der Zähler bleibt
    bei 0 statt auf 1 zu steigen (das Histogramm selbst bleibt im Export
    sichtbar, das ist normales `prometheus_client`-Verhalten für einmal
    registrierte Metriken - entscheidend ist der unveränderte Wert)."""
    monkeypatch.setattr(app.state.upload_duration_sensor, "_is_active", lambda name: False)
    upload(client)
    response = client.get("/metrics")
    assert "document_upload_duration_count 0.0" in response.text


def test_create_and_get_document(client):
    response = upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["current_version_number"] == 1
    assert body["title"] == "Vertrag"

    get_response = client.get(f"/documents/{body['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == body["id"]


def test_create_document_publishes_event_with_actor(client, monkeypatch):
    """First-class Actor-Feld (5.4b-Voraussetzung, seit P7-S2) - der
    publizierte `document.created`-Event trägt den Hochlader als `actor`,
    nicht nur ad-hoc unter `payload["created_by"]`."""
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    response = upload(client, created_by="alice")
    assert response.status_code == 201

    created_events = [e for e in published if e.event_type == "document.created"]
    assert len(created_events) == 1
    assert created_events[0].actor == "alice"


def test_download_current_content_roundtrips_bytes(client):
    body = upload(client, content=b"Original-Inhalt").json()

    response = client.get(f"/documents/{body['id']}/content")
    assert response.status_code == 200
    assert response.content == b"Original-Inhalt"


def test_download_content_returns_404_instead_of_crashing_if_object_missing(client):
    """Regressionstest: eine Inkonsistenz zwischen Document Service (kennt die
    Version) und Storage Service (Objekt fehlt, z. B. weil dessen Metadaten-
    Zeile verloren ging) darf nicht zu einem unbehandelten 500 führen (siehe
    storage_client.ObjectNotFoundError)."""
    body = upload(client, content=b"wird gleich verwaist").json()
    document_id = body["id"]
    checksum = client.get(f"/documents/{document_id}/versions/1").json()["checksum_sha256"]

    delete_response = httpx.delete(
        f"{STORAGE_SERVICE_URL}/objects/documents/{document_id}/{checksum}"
    )
    assert delete_response.status_code == 204

    response = client.get(f"/documents/{document_id}/content")
    assert response.status_code == 404

    response_by_version = client.get(f"/documents/{document_id}/versions/1/content")
    assert response_by_version.status_code == 404


def test_get_unknown_document_returns_404(client):
    response = client.get("/documents/does-not-exist")
    assert response.status_code == 404


def test_list_documents_by_folder(client):
    in_folder = upload(client, title="Im Ordner", folder_id="root").json()
    upload(client, title="Ohne Ordner")

    response = client.get("/documents", params={"folder_id": "root"})

    assert response.status_code == 200
    ids = [d["id"] for d in response.json()]
    assert in_folder["id"] in ids
    assert all(d["folder_id"] == "root" for d in response.json())


def test_list_documents_excludes_deleted(client):
    body = upload(client, folder_id="root").json()
    client.delete(f"/documents/{body['id']}", params={"deleted_by": "alice"})

    response = client.get("/documents", params={"folder_id": "root"})

    assert body["id"] not in [d["id"] for d in response.json()]


def test_list_documents_unknown_folder_returns_empty(client):
    response = client.get("/documents", params={"folder_id": "does-not-exist"})
    assert response.status_code == 200
    assert response.json() == []


def test_create_document_rejects_infected_upload(client):
    response = upload(client, content=EICAR_SIGNATURE)

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "virus_detected"
    assert response.json()["detail"]["threat_name"] == "Eicar-Test-Signature"


def test_checkin_rejects_infected_version_without_creating_it(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]

    response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("infiziert.pdf", EICAR_SIGNATURE, "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "virus_detected"

    versions = client.get(f"/documents/{document_id}/versions").json()
    assert len(versions) == 1

    download = client.get(f"/documents/{document_id}/content")
    assert download.content == b"v1"


def test_checkin_normal_version_and_download(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]

    response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("vertrag.pdf", b"v2", "application/pdf")},
    )
    assert response.status_code == 201
    result = response.json()
    assert result["is_conflict"] is False
    assert result["version"]["version_number"] == 2

    download = client.get(f"/documents/{document_id}/content")
    assert download.content == b"v2"


def test_checkin_stale_base_produces_conflict_copy_without_moving_current(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]

    client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "bob"},
        files={"file": ("vertrag.pdf", b"bobs-version", "application/pdf")},
    )

    conflict_response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("vertrag.pdf", b"alice-stale-version", "application/pdf")},
    )
    assert conflict_response.status_code == 201
    conflict_result = conflict_response.json()
    assert conflict_result["is_conflict"] is True
    assert "_conflict_alice_" in conflict_result["version"]["filename"]

    current = client.get(f"/documents/{document_id}/content")
    assert current.content == b"bobs-version"


def test_lock_conflict_returns_409(client):
    body = upload(client).json()
    document_id = body["id"]

    first = client.post(
        f"/documents/{document_id}/lock",
        json={"locked_by": "alice", "session_id": "s1"},
    )
    assert first.status_code == 201

    second = client.post(
        f"/documents/{document_id}/lock",
        json={"locked_by": "bob", "session_id": "s2"},
    )
    assert second.status_code == 409


def test_release_lock_wrong_holder_returns_403(client):
    body = upload(client).json()
    document_id = body["id"]
    client.post(f"/documents/{document_id}/lock", json={"locked_by": "alice", "session_id": "s1"})

    response = client.request(
        "DELETE", f"/documents/{document_id}/lock", json={"released_by": "bob"}
    )
    assert response.status_code == 403


def test_force_release_then_conflicting_checkin(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]
    client.post(f"/documents/{document_id}/lock", json={"locked_by": "alice", "session_id": "s1"})

    force_response = client.post(
        f"/documents/{document_id}/lock/force-release",
        json={"released_by": "admin", "reason": "Mitarbeiter im Urlaub"},
    )
    assert force_response.status_code == 200
    assert force_response.json()["status"] == "released"
    assert force_response.json()["lock"]["locked_by"] == "alice"

    # Sperre ist wirklich weg - Bob kann jetzt normal einchecken.
    bob_response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "bob"},
        files={"file": ("vertrag.pdf", b"bobs-version", "application/pdf")},
    )
    assert bob_response.json()["is_conflict"] is False

    # Alice versucht danach ebenfalls einzuchecken - landet als Konfliktkopie.
    alice_response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("vertrag.pdf", b"alice-stale", "application/pdf")},
    )
    assert alice_response.json()["is_conflict"] is True


def test_force_release_with_approval_required_defers_execution(client):
    """Vier-Augen-Retrofit (4.3, P6-S4): mit aktivierter Genehmigungspflicht
    wird die Sperre NICHT sofort aufgehoben - echte Integration gegen den
    lokal laufenden permission-service, gleiches "kein Mocking von
    Sibling-Services"-Muster wie folder_client/object_type_client."""
    httpx.put(
        f"{PERMISSION_SERVICE_URL}/approval-config/document.force_unlock",
        json={"requires_approval": True},
    )
    try:
        body = upload(client, content=b"v1").json()
        document_id = body["id"]
        client.post(
            f"/documents/{document_id}/lock", json={"locked_by": "alice", "session_id": "s1"}
        )

        response = client.post(
            f"/documents/{document_id}/lock/force-release",
            json={"released_by": "admin", "reason": "Test"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "pending_approval"
        assert result["approval_request_id"] is not None
        assert result["lock"] is None

        # Sperre ist weiterhin aktiv - keine sofortige Ausfuehrung, die
        # tatsaechliche Ausfuehrung folgt asynchron ueber consumer.py, sobald
        # das Approval-Event eintrifft (siehe test_consumer.py).
        lock_response = client.get(f"/documents/{document_id}/lock")
        assert lock_response.json()["locked_by"] == "alice"
    finally:
        httpx.put(
            f"{PERMISSION_SERVICE_URL}/approval-config/document.force_unlock",
            json={"requires_approval": False},
        )


def test_update_document_metadata(client):
    body = upload(client, title="Alt").json()
    document_id = body["id"]

    response = client.patch(
        f"/documents/{document_id}",
        json={"title": "Neu", "attributes": {"foo": "bar"}},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "Neu"
    assert updated["attributes"] == {"foo": "bar"}

    get_response = client.get(f"/documents/{document_id}")
    assert get_response.json()["title"] == "Neu"


def test_update_document_metadata_partial_update_keeps_title(client):
    body = upload(client, title="Bleibt").json()
    document_id = body["id"]

    response = client.patch(f"/documents/{document_id}", json={"attributes": {"foo": "bar"}})

    assert response.status_code == 200
    assert response.json()["title"] == "Bleibt"


def test_update_document_metadata_unknown_document_returns_404(client):
    response = client.patch("/documents/does-not-exist", json={"title": "x"})
    assert response.status_code == 404


def test_update_document_moves_to_new_folder(client):
    """P12-S1 (WebDAV-Connector-Nutzerwunsch): Dokumente konnten bislang nicht
    zwischen Ordnern verschoben werden, anders als folder-service seit P3-S3
    für Ordner selbst (`PATCH /folders/{id}` mit `parent_id`)."""
    new_folder = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders",
        json={"name": "Zielordner", "parent_id": "root", "created_by": "alice"},
    ).json()
    document_id = upload(client, folder_id="root").json()["id"]

    response = client.patch(f"/documents/{document_id}", json={"folder_id": new_folder["id"]})

    assert response.status_code == 200
    assert response.json()["folder_id"] == new_folder["id"]
    get_response = client.get(f"/documents/{document_id}")
    assert get_response.json()["folder_id"] == new_folder["id"]


def test_update_document_move_to_unknown_folder_returns_400(client):
    document_id = upload(client, folder_id="root").json()["id"]

    response = client.patch(f"/documents/{document_id}", json={"folder_id": "does-not-exist"})

    assert response.status_code == 400
    assert response.json()["detail"] == "folder_id 'does-not-exist' unbekannt"
    # Unveraendert bei fehlgeschlagenem Move.
    assert client.get(f"/documents/{document_id}").json()["folder_id"] == "root"


def test_update_document_without_folder_id_keeps_current_folder(client):
    document_id = upload(client, title="Bleibt im Ordner", folder_id="root").json()["id"]

    response = client.patch(f"/documents/{document_id}", json={"title": "Umbenannt"})

    assert response.status_code == 200
    assert response.json()["folder_id"] == "root"


def test_create_document_discards_client_supplied_kennzeichen(client):
    response = upload(client, attributes='{"Kennzeichen": "FAKE-001"}')
    assert response.status_code == 201
    assert "Kennzeichen" not in response.json()["attributes"]


def test_update_kennzeichen_without_admin_role_returns_403(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]

    response = client.patch(
        f"/documents/{document_id}",
        json={"attributes": {"foo": "bar", "Kennzeichen": "2026-001"}},
    )
    assert response.status_code == 403


def test_update_kennzeichen_with_admin_role_succeeds(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]

    response = client.patch(
        f"/documents/{document_id}",
        json={"attributes": {"foo": "bar", "Kennzeichen": "2026-001"}},
        headers={"X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 200
    assert response.json()["attributes"]["Kennzeichen"] == "2026-001"


def test_update_kennzeichen_with_other_roles_still_returns_403(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]

    response = client.patch(
        f"/documents/{document_id}",
        json={"attributes": {"foo": "bar", "Kennzeichen": "2026-001"}},
        headers={"X-DMS-Roles": "some-other-role,another-role"},
    )
    assert response.status_code == 403


def test_update_attributes_without_touching_kennzeichen_needs_no_role(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]

    response = client.patch(f"/documents/{document_id}", json={"attributes": {"foo": "baz"}})
    assert response.status_code == 200
    assert response.json()["attributes"] == {"foo": "baz"}


def test_removing_existing_kennzeichen_via_attribute_replace_needs_admin_role(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]
    client.patch(
        f"/documents/{document_id}",
        json={"attributes": {"Kennzeichen": "2026-001"}},
        headers={"X-DMS-Roles": "dms-admin"},
    )

    response = client.patch(f"/documents/{document_id}", json={"attributes": {"foo": "bar"}})
    assert response.status_code == 403


def test_upload_content_type_is_sniffed_not_trusted(client):
    """P5d-S1: der vom Browser gesendete Header wird nicht mehr übernommen -
    hier klar sichtbar, da `upload()` Klartext-Inhalt als "application/pdf"
    deklariert, aber das tatsächliche Byte-Sniffing "text/plain" ermittelt."""
    body = upload(client, content=b"Hallo Welt").json()
    version = client.get(f"/documents/{body['id']}/versions/1").json()
    assert version["content_type"] == "text/plain"


def test_checkin_content_type_is_sniffed_not_trusted(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]

    client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("daten.json", b'{"a": 1}', "application/pdf")},
    )

    version = client.get(f"/documents/{document_id}/versions/2").json()
    assert version["content_type"] == "application/json"


def test_upload_rejects_content_type_not_on_whitelist(client):
    config_response = client.put(
        "/upload-config", json={"allowed_content_types": ["application/pdf"]}
    )
    assert config_response.status_code == 200

    response = upload(client, content=b"Hallo Welt")  # sniffed als text/plain

    assert response.status_code == 400


def test_upload_allows_content_type_on_whitelist(client):
    client.put("/upload-config", json={"allowed_content_types": ["text/plain"]})

    response = upload(client, content=b"Hallo Welt")

    assert response.status_code == 201


def test_upload_config_empty_whitelist_means_no_restriction(client):
    response = client.get("/upload-config")
    assert response.status_code == 200
    assert response.json()["allowed_content_types"] == []

    response = upload(client, content=b"Hallo Welt")
    assert response.status_code == 201


def test_put_upload_config_persists(client):
    put_response = client.put(
        "/upload-config", json={"allowed_content_types": ["application/pdf", "text/plain"]}
    )
    assert put_response.status_code == 200
    assert put_response.json()["allowed_content_types"] == ["application/pdf", "text/plain"]

    get_response = client.get("/upload-config")
    assert get_response.json()["allowed_content_types"] == ["application/pdf", "text/plain"]


def test_audit_trace_config_defaults_to_logging_everything(client):
    response = client.get("/audit-trace-config")
    assert response.status_code == 200
    body = response.json()
    assert body["log_viewed"] is True
    assert body["log_downloaded"] is True


def test_put_audit_trace_config_persists(client):
    put_response = client.put(
        "/audit-trace-config", json={"log_viewed": False, "log_downloaded": True}
    )
    assert put_response.status_code == 200
    assert put_response.json()["log_viewed"] is False

    get_response = client.get("/audit-trace-config")
    assert get_response.json()["log_viewed"] is False
    assert get_response.json()["log_downloaded"] is True


def test_audit_trace_role_override_create_list_delete(client):
    create_response = client.put(
        "/audit-trace-role-overrides/auditor",
        json={"log_viewed": True, "log_downloaded": None},
    )
    assert create_response.status_code == 200
    assert create_response.json()["role"] == "auditor"

    list_response = client.get("/audit-trace-role-overrides")
    assert [o["role"] for o in list_response.json()] == ["auditor"]

    delete_response = client.delete("/audit-trace-role-overrides/auditor")
    assert delete_response.status_code == 204
    assert client.get("/audit-trace-role-overrides").json() == []


def test_delete_unknown_audit_trace_role_override_returns_404(client):
    response = client.delete("/audit-trace-role-overrides/does-not-exist")
    assert response.status_code == 404


def _published_event_types(published: list[Event]) -> list[str]:
    return [e.event_type for e in published]


def test_get_document_publishes_viewed_event_by_default(client, monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    body = upload(client).json()
    published.clear()  # nur den Read-Zugriff selbst betrachten

    response = client.get(f"/documents/{body['id']}", headers={"X-DMS-Username": "bob"})
    assert response.status_code == 200

    viewed = [e for e in published if e.event_type == "document.viewed"]
    assert len(viewed) == 1
    assert viewed[0].subject == body["id"]
    assert viewed[0].actor == "bob"


def test_download_content_publishes_downloaded_event_by_default(client, monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    body = upload(client).json()
    published.clear()

    response = client.get(f"/documents/{body['id']}/content", headers={"X-DMS-Username": "bob"})
    assert response.status_code == 200

    downloaded = [e for e in published if e.event_type == "document.downloaded"]
    assert len(downloaded) == 1
    assert downloaded[0].actor == "bob"


def test_get_document_does_not_publish_when_base_config_disables_viewed(client, monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    client.put("/audit-trace-config", json={"log_viewed": False, "log_downloaded": True})
    body = upload(client).json()
    published.clear()

    client.get(f"/documents/{body['id']}")

    assert "document.viewed" not in _published_event_types(published)


def test_role_override_can_disable_viewed_for_specific_role(client, monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    client.put(
        "/audit-trace-role-overrides/quiet-role",
        json={"log_viewed": False, "log_downloaded": None},
    )
    body = upload(client).json()
    published.clear()

    client.get(f"/documents/{body['id']}", headers={"X-DMS-Roles": "quiet-role"})

    assert "document.viewed" not in _published_event_types(published)


def test_role_override_conflict_logging_wins(client, monkeypatch):
    """Sicherheits-first-Konfliktregel: Basis aus, aber eine der zugewiesenen
    Rollen verlangt explizit Protokollierung -> Event wird trotzdem publiziert."""
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    client.put("/audit-trace-config", json={"log_viewed": False, "log_downloaded": False})
    client.put(
        "/audit-trace-role-overrides/quiet-role",
        json={"log_viewed": False, "log_downloaded": None},
    )
    client.put(
        "/audit-trace-role-overrides/loud-role",
        json={"log_viewed": True, "log_downloaded": None},
    )
    body = upload(client).json()
    published.clear()

    client.get(f"/documents/{body['id']}", headers={"X-DMS-Roles": "quiet-role,loud-role"})

    assert "document.viewed" in _published_event_types(published)


def test_delete_document(client):
    body = upload(client).json()
    document_id = body["id"]

    response = client.request("DELETE", f"/documents/{document_id}?deleted_by=admin")
    assert response.status_code == 200
    assert response.json()["deleted_at"] is not None


def test_create_document_with_derived_from_fields_creates_independent_document(client):
    """Bearbeitungskopie (2.3, P6-S3, z. B. Schwaerzung fuer die Akteneinsicht):
    kein neues Endpunkt noetig - die Herkunftsfelder laufen ueber die normale
    Upload-Pipeline und das Ergebnis ist ein ganz eigenstaendiges Dokument."""
    original = upload(client, title="Original").json()

    copy_response = upload(
        client,
        title="Schwaerzung",
        content=b"geschwaerzter Inhalt",
        derived_from_document_id=original["id"],
        derived_from_version_number=1,
        originating_case_id="case-123",
    )
    assert copy_response.status_code == 201
    copy = copy_response.json()
    assert copy["id"] != original["id"]
    assert copy["derived_from_document_id"] == original["id"]
    assert copy["derived_from_version_number"] == 1
    assert copy["originating_case_id"] == "case-123"

    # Unabhaengige Versionierung/Auditierung - eigener current_version_number,
    # das Original bleibt unveraendert.
    assert copy["current_version_number"] == 1
    original_still_unchanged = client.get(f"/documents/{original['id']}").json()
    assert original_still_unchanged["derived_from_document_id"] is None


def test_create_document_without_derived_fields_has_null_origin(client):
    body = upload(client).json()
    assert body["derived_from_document_id"] is None
    assert body["derived_from_version_number"] is None
    assert body["originating_case_id"] is None


def test_create_document_derived_from_without_version_number_returns_400(client):
    original = upload(client).json()
    response = upload(client, derived_from_document_id=original["id"])
    assert response.status_code == 400


def test_create_document_derived_from_unknown_version_returns_400(client):
    original = upload(client).json()
    response = upload(
        client, derived_from_document_id=original["id"], derived_from_version_number=99
    )
    assert response.status_code == 400


def test_create_document_derived_from_unknown_document_returns_400(client):
    response = upload(
        client, derived_from_document_id="does-not-exist", derived_from_version_number=1
    )
    assert response.status_code == 400


# --- Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) ---------


def test_put_retention_sets_fields(client):
    document_id = upload(client).json()["id"]

    response = client.put(
        f"/documents/{document_id}/retention",
        json={"retention_until": "2030-01-01T00:00:00Z", "full_deletion": True, "reason": "Test"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["retention_until"].startswith("2030-01-01")
    assert body["full_deletion"] is True
    assert body["pending_deletion_reason"] == "Test"


def test_put_retention_unknown_document_returns_404(client):
    response = client.put(
        "/documents/does-not-exist/retention",
        json={"retention_until": None, "full_deletion": False},
    )
    assert response.status_code == 404


def test_put_retention_requires_reason_when_configured(client):
    client.put(
        "/retention-config", json={"deletion_reason_required": True, "reminder_lead_days": None}
    )
    document_id = upload(client).json()["id"]

    response = client.put(
        f"/documents/{document_id}/retention",
        json={"retention_until": "2030-01-01T00:00:00Z", "full_deletion": True},
    )

    assert response.status_code == 422
    # Aufräumen für nachfolgende Tests.
    client.put(
        "/retention-config", json={"deletion_reason_required": False, "reminder_lead_days": None}
    )


def test_put_retention_reason_not_required_for_regular_soft_delete(client):
    """Löschgrund-Pflicht (5.2a) gilt nur für `full_deletion=True` - eine
    reguläre Aufbewahrungsfrist ohne Zwangslöschung braucht keinen Grund."""
    client.put(
        "/retention-config", json={"deletion_reason_required": True, "reminder_lead_days": None}
    )
    document_id = upload(client).json()["id"]

    response = client.put(
        f"/documents/{document_id}/retention",
        json={"retention_until": "2030-01-01T00:00:00Z", "full_deletion": False},
    )

    assert response.status_code == 200
    client.put(
        "/retention-config", json={"deletion_reason_required": False, "reminder_lead_days": None}
    )


def test_trash_document_immediate_by_default(client):
    """Löschantrag-Workflow (5.2, seit P7-S1c) - ohne konfigurierte
    Genehmigungspflicht bleibt das Verhalten identisch zum bestehenden
    `DELETE /documents/{id}`: sofortige Ausführung."""
    document_id = upload(client).json()["id"]

    response = client.post(f"/documents/{document_id}/trash", json={"deleted_by": "alice"})

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "trashed"
    assert result["document"]["deleted_at"] is not None
    assert result["approval_request_id"] is None


def test_trash_document_with_approval_required_defers_execution(client):
    """Echte Integration gegen den lokal laufenden permission-service,
    gleiches Muster wie `test_force_release_with_approval_required_defers_
    execution` (4.3, P6-S4)."""
    httpx.put(
        f"{PERMISSION_SERVICE_URL}/approval-config/document.delete",
        json={"requires_approval": True},
    )
    try:
        document_id = upload(client).json()["id"]

        response = client.post(f"/documents/{document_id}/trash", json={"deleted_by": "alice"})

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "pending_approval"
        assert result["approval_request_id"] is not None
        assert result["document"] is None

        # Dokument ist weiterhin nicht gelöscht - die tatsächliche Ausführung
        # folgt asynchron über consumer.py (siehe test_consumer.py).
        assert client.get(f"/documents/{document_id}").json()["deleted_at"] is None
    finally:
        httpx.put(
            f"{PERMISSION_SERVICE_URL}/approval-config/document.delete",
            json={"requires_approval": False},
        )


def test_restore_document_within_period(client):
    document_id = upload(client).json()["id"]
    client.request("DELETE", f"/documents/{document_id}?deleted_by=admin")

    response = client.post(f"/documents/{document_id}/restore")

    assert response.status_code == 200
    assert response.json()["deleted_at"] is None


def test_restore_document_not_deleted_returns_409(client):
    document_id = upload(client).json()["id"]

    response = client.post(f"/documents/{document_id}/restore")

    assert response.status_code == 409


def test_restore_unknown_document_returns_404(client):
    response = client.post("/documents/does-not-exist/restore")
    assert response.status_code == 404


def test_list_deleted_documents_shows_only_trash(client):
    kept = upload(client, folder_id="root").json()
    deleted = upload(client, folder_id="root").json()
    client.request("DELETE", f"/documents/{deleted['id']}?deleted_by=admin")

    response = client.get("/documents/deleted", params={"folder_id": "root"})

    assert response.status_code == 200
    ids = [d["id"] for d in response.json()]
    assert deleted["id"] in ids
    assert kept["id"] not in ids
    # Der reguläre Listing-Endpunkt zeigt weiterhin nur nicht-gelöschte Dokumente.
    regular = client.get("/documents", params={"folder_id": "root"}).json()
    assert deleted["id"] not in [d["id"] for d in regular]


def test_cascade_trash_and_restore_roundtrip(client):
    """Interner Kaskaden-Weg für folder-service (5.2, seit P7-S1b) - siehe
    docs/services/folder-service.md."""
    document_id = upload(client, folder_id="root").json()["id"]

    trash_response = client.post(
        "/documents/cascade-trash",
        json={
            "folder_ids": ["root"],
            "via_folder_id": "root",
            "deleted_by": "system:folder-cascade",
        },
    )
    assert trash_response.status_code == 200
    assert trash_response.json()["document_ids"] == [document_id]
    assert client.get(f"/documents/{document_id}").json()["deleted_at"] is not None

    restore_response = client.post("/documents/cascade-restore", json={"via_folder_id": "root"})
    assert restore_response.status_code == 200
    assert restore_response.json()["document_ids"] == [document_id]
    assert client.get(f"/documents/{document_id}").json()["deleted_at"] is None


def test_count_active_excludes_deleted(client):
    upload(client, folder_id="root")
    deleted_id = upload(client, folder_id="root").json()["id"]
    client.request("DELETE", f"/documents/{deleted_id}?deleted_by=admin")

    response = client.post("/documents/count-active", json={"folder_ids": ["root"]})

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_count_active_total_counts_across_all_folders(client):
    """Installationsweite Dokumentenzahl (9.1, seit P9-S1) - `license-service`s
    Nutzungspruefung."""
    upload(client, folder_id="root")
    deleted_id = upload(client, folder_id="root").json()["id"]
    client.request("DELETE", f"/documents/{deleted_id}?deleted_by=admin")

    response = client.get("/documents/count-active-total")

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_legal_hold_lifecycle(client):
    document_id = upload(client).json()["id"]

    create_response = client.post(
        "/legal-holds",
        json={"document_id": document_id, "set_by": "alice", "reason": "Rechtsstreit"},
    )
    assert create_response.status_code == 201
    hold = create_response.json()
    assert hold["released_at"] is None

    list_response = client.get(
        "/legal-holds", params={"document_id": document_id, "active_only": True}
    )
    assert len(list_response.json()) == 1

    release_response = client.post(
        f"/legal-holds/{hold['id']}/release", json={"released_by": "bob"}
    )
    assert release_response.status_code == 200
    assert release_response.json()["released_by"] == "bob"

    list_after_release = client.get(
        "/legal-holds", params={"document_id": document_id, "active_only": True}
    )
    assert list_after_release.json() == []


def test_release_legal_hold_twice_returns_409(client):
    document_id = upload(client).json()["id"]
    hold = client.post(
        "/legal-holds", json={"document_id": document_id, "set_by": "alice", "reason": None}
    ).json()
    client.post(f"/legal-holds/{hold['id']}/release", json={"released_by": "alice"})

    response = client.post(f"/legal-holds/{hold['id']}/release", json={"released_by": "alice"})

    assert response.status_code == 409


def test_create_legal_hold_unknown_document_returns_404(client):
    response = client.post(
        "/legal-holds", json={"document_id": "does-not-exist", "set_by": "alice", "reason": None}
    )
    assert response.status_code == 404


def test_deletion_register_empty_by_default(client):
    response = client.get("/deletion-register")
    assert response.status_code == 200


def test_reconcile_restore_deletion_requires_admin_role(client):
    document_id = upload(client).json()["id"]
    response = client.post(
        f"/documents/{document_id}/reconcile-restore-deletion",
        json={"original_entry_id": "led-1", "reason": None},
    )
    assert response.status_code == 403
    # Unveraendert - der 403 darf keine Nebenwirkung haben.
    assert client.get(f"/documents/{document_id}").status_code == 200


def test_reconcile_restore_deletion_performs_real_forced_deletion(client):
    """10.4/P11-S4: derselbe Mechanismus wie die ursprüngliche Zwangslöschung
    (execute_forced_deletion) - Dokument ist danach wirklich weg, mit einem
    echten DeletionRegisterEntry als Nachweis."""
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/reconcile-restore-deletion",
        json={"original_entry_id": "led-42", "reason": "Restore-Abgleich"},
        headers={"X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 204
    assert client.get(f"/documents/{document_id}").status_code == 404

    register = client.get("/deletion-register", params={"document_id": document_id}).json()
    assert len(register) == 1
    assert register[0]["trigger"] == "forced_deletion"
    assert register[0]["triggered_by"] == "system:restore-reconciliation"


def test_reconcile_restore_deletion_unknown_document_returns_404(client):
    response = client.post(
        "/documents/does-not-exist/reconcile-restore-deletion",
        json={"original_entry_id": "led-1", "reason": None},
        headers={"X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 404
    assert client.get("/deletion-register").json() == []


def test_retention_config_get_and_put(client):
    get_response = client.get("/retention-config")
    assert get_response.status_code == 200
    assert get_response.json()["deletion_reason_required"] is False

    put_response = client.put(
        "/retention-config", json={"deletion_reason_required": True, "reminder_lead_days": 5}
    )
    assert put_response.status_code == 200
    assert put_response.json()["reminder_lead_days"] == 5
    # Aufräumen.
    client.put(
        "/retention-config", json={"deletion_reason_required": False, "reminder_lead_days": None}
    )


def test_trash_config_get_and_put(client):
    get_response = client.get("/trash-config")
    assert get_response.status_code == 200
    assert get_response.json()["restore_period_days"] == 30

    put_response = client.put("/trash-config", json={"restore_period_days": 10})
    assert put_response.status_code == 200
    assert put_response.json()["restore_period_days"] == 10
    # Aufräumen.
    client.put("/trash-config", json={"restore_period_days": 30})


def test_archive_request_and_status_roundtrip(client):
    body = upload(client).json()
    document_id = body["id"]

    status_response = client.get(f"/documents/{document_id}/archive-status")
    assert status_response.status_code == 200
    assert status_response.json()["archive_after"] is None

    request_response = client.post(f"/documents/{document_id}/archive-request")
    assert request_response.status_code == 200
    assert request_response.json()["archive_after"] is not None

    status_after = client.get(f"/documents/{document_id}/archive-status")
    assert status_after.json()["archive_after"] is not None
    assert status_after.json()["archived_at"] is None


def test_documents_due_for_archival_lists_fällige_documents(client):
    body = upload(client).json()
    document_id = body["id"]
    client.post(f"/documents/{document_id}/archive-request")

    response = client.get("/documents/due-for-archival")

    assert response.status_code == 200
    assert document_id in [d["id"] for d in response.json()]


def test_mark_archived_dehydrated_rehydrated_lifecycle(client):
    body = upload(client).json()
    document_id = body["id"]

    archived = client.put(f"/documents/{document_id}/archived", json={"archive_format": "pdf_a"})
    assert archived.status_code == 200
    assert archived.json()["archive_format"] == "pdf_a"
    assert archived.json()["archived_at"] is not None

    dehydrated = client.put(f"/documents/{document_id}/dehydrated")
    assert dehydrated.status_code == 200
    assert dehydrated.json()["dehydrated_at"] is not None

    rehydrated = client.put(f"/documents/{document_id}/rehydrated")
    assert rehydrated.status_code == 200
    assert rehydrated.json()["dehydrated_at"] is None


# --- Öffentlicher Freigabelink (4.2a, P14-S10) ------------------------------


def _future(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def test_share_link_config_get_and_put_roundtrip(client):
    get_response = client.get("/share-link-config")
    assert get_response.status_code == 200
    assert get_response.json() == {
        "enabled": True,
        "max_validity_days": 30,
        "updated_at": get_response.json()["updated_at"],
    }

    put_response = client.put("/share-link-config", json={"enabled": True, "max_validity_days": 7})
    assert put_response.status_code == 200
    assert put_response.json()["max_validity_days"] == 7
    # Aufräumen.
    client.put("/share-link-config", json={"enabled": True, "max_validity_days": 30})


def test_create_share_link_requires_principal_header(client):
    document_id = upload(client).json()["id"]

    response = client.post(f"/documents/{document_id}/share-links", json={"expires_at": _future(1)})
    assert response.status_code == 401


def test_create_share_link_returns_404_for_unknown_document(client):
    response = client.post(
        "/documents/does-not-exist/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": "alice"},
    )
    assert response.status_code == 404


def test_create_share_link_rejects_expiry_in_the_past(client):
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(-1)},
        headers={"X-DMS-Principal": "alice"},
    )
    assert response.status_code == 400


def test_create_share_link_rejects_expiry_beyond_max_validity(client):
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(365)},
        headers={"X-DMS-Principal": "alice"},
    )
    assert response.status_code == 400


def test_create_share_link_requires_read_permission(client):
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": f"principal-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_create_share_link_returns_404_when_feature_disabled(client):
    document_id = upload(client).json()["id"]
    client.put("/share-link-config", json={"enabled": False, "max_validity_days": 30})
    try:
        response = client.post(
            f"/documents/{document_id}/share-links",
            json={"expires_at": _future(1)},
            headers={"X-DMS-Principal": "alice"},
        )
        assert response.status_code == 404
    finally:
        client.put("/share-link-config", json={"enabled": True, "max_validity_days": 30})


def test_create_share_link_succeeds_and_publishes_event(client, monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": principal},
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["token"]) >= 32
    assert body["document_id"] == document_id
    assert body["created_by"] == principal
    assert body["revoked_at"] is None

    created_events = [e for e in published if e.event_type == "document.share_link.created"]
    assert len(created_events) == 1


def test_list_share_links_requires_read_permission(client):
    document_id = upload(client).json()["id"]

    response = client.get(
        f"/documents/{document_id}/share-links",
        headers={"X-DMS-Principal": f"principal-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_list_share_links_returns_all_links_for_the_document(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]

    client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": principal},
    )
    client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(2)},
        headers={"X-DMS-Principal": principal},
    )

    response = client.get(
        f"/documents/{document_id}/share-links", headers={"X-DMS-Principal": principal}
    )
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_revoke_share_link_requires_creator_or_admin_role(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]
    token = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": principal},
    ).json()["token"]

    forbidden = client.delete(f"/share-links/{token}", headers={"X-DMS-Principal": "someone-else"})
    assert forbidden.status_code == 403

    admin = client.delete(
        f"/share-links/{token}",
        headers={"X-DMS-Principal": "an-admin", "X-DMS-Roles": "dms-admin"},
    )
    assert admin.status_code == 204


def test_revoke_share_link_by_creator_succeeds(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]
    token = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": principal},
    ).json()["token"]

    response = client.delete(f"/share-links/{token}", headers={"X-DMS-Principal": principal})
    assert response.status_code == 204


def test_revoke_share_link_unknown_token_returns_404(client):
    response = client.delete("/share-links/does-not-exist", headers={"X-DMS-Principal": "alice"})
    assert response.status_code == 404


def test_public_share_link_returns_minimal_metadata_without_authentication(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client, title="Öffentliches Dokument", content=b"geheimer Inhalt").json()[
        "id"
    ]
    token = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": principal},
    ).json()["token"]

    response = client.get("/public/share-links", params={"token": token})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "title": "Öffentliches Dokument",
        "content_type": "text/plain",
        "size_bytes": len(b"geheimer Inhalt"),
        "expires_at": body["expires_at"],
    }
    assert "attributes" not in body
    assert "created_by" not in body


def test_public_share_link_content_downloads_original_bytes(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client, content=b"Freigegebener Inhalt").json()["id"]
    token = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": principal},
    ).json()["token"]

    response = client.get("/public/share-links/content", params={"token": token})
    assert response.status_code == 200
    assert response.content == b"Freigegebener Inhalt"


def test_public_share_link_unknown_token_returns_404(client):
    response = client.get("/public/share-links", params={"token": "does-not-exist"})
    assert response.status_code == 404


def test_public_share_link_revoked_returns_410(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]
    token = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": principal},
    ).json()["token"]
    client.delete(f"/share-links/{token}", headers={"X-DMS-Principal": principal})

    response = client.get("/public/share-links", params={"token": token})
    assert response.status_code == 410


def test_public_share_link_returns_404_when_feature_disabled(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]
    token = client.post(
        f"/documents/{document_id}/share-links",
        json={"expires_at": _future(1)},
        headers={"X-DMS-Principal": principal},
    ).json()["token"]

    client.put("/share-link-config", json={"enabled": False, "max_validity_days": 30})
    try:
        response = client.get("/public/share-links", params={"token": token})
        assert response.status_code == 404
    finally:
        client.put("/share-link-config", json={"enabled": True, "max_validity_days": 30})


def test_archive_endpoints_return_404_for_unknown_document(client):
    assert client.post("/documents/does-not-exist/archive-request").status_code == 404
    assert client.get("/documents/does-not-exist/archive-status").status_code == 404
    assert (
        client.put(
            "/documents/does-not-exist/archived", json={"archive_format": "pdf_a"}
        ).status_code
        == 404
    )
    assert client.put("/documents/does-not-exist/dehydrated").status_code == 404
    assert client.put("/documents/does-not-exist/rehydrated").status_code == 404


def test_has_active_hold_reflects_legal_hold_state(client):
    body = upload(client).json()
    document_id = body["id"]

    assert client.get(f"/documents/{document_id}/has-active-hold").json() == {
        "has_active_hold": False
    }

    hold_response = client.post(
        "/legal-holds", json={"document_id": document_id, "set_by": "alice"}
    )
    assert hold_response.status_code == 201

    assert client.get(f"/documents/{document_id}/has-active-hold").json() == {
        "has_active_hold": True
    }
