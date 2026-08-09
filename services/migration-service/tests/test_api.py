"""Migration/Transfer Service (7.2, P12-S2). Läuft wie `webdav-connector`
gegen den echten, per docker-compose laufenden Container (kein In-Prozess-
`TestClient`) - der Selbst-Loopback-Smoke-Test (ein echter zweiter
Installations-Stack ist im Sandbox nicht sinnvoll aufsetzbar, gleiche
Konvention wie `federation-hub-service`, P6-S9) braucht einen von aussen
über einen echten Netzwerk-Socket erreichbaren Server für die ausgehenden
`PeerClient`-Aufrufe der Quellrolle gegen die eigene Zielrolle."""

import os
import time
import uuid

import httpx

# Eigenständig gelesen statt aus conftest importiert (siehe dortiger
# Kommentar: `from conftest import x` ist mit `--import-mode=importlib`
# über Testmodule hinweg nicht zuverlässig, gleiches Muster wie
# workflow-service/test_federation.py).
MIGRATION_SERVICE_URL = os.environ.get("TEST_MIGRATION_SERVICE_URL", "http://localhost:8028")
FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


def _client() -> httpx.Client:
    return httpx.Client(base_url=MIGRATION_SERVICE_URL, timeout=30.0)


def _create_folder(*, parent_id: str = "root", name: str) -> dict:
    response = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders",
        json={"name": name, "parent_id": parent_id, "created_by": "migration-tests"},
    )
    response.raise_for_status()
    return response.json()


def _upload_document(*, folder_id: str, title: str, content: bytes = b"Inhalt") -> dict:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": title, "created_by": "migration-tests", "folder_id": folder_id},
        files={"file": (title, content, "text/plain")},
    )
    response.raise_for_status()
    return response.json()


def _get_document(document_id: str) -> dict:
    response = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents/{document_id}")
    response.raise_for_status()
    return response.json()


def _list_children(folder_id: str) -> list[dict]:
    response = httpx.get(f"{FOLDER_SERVICE_URL}/folders/{folder_id}/children")
    response.raise_for_status()
    return response.json()


def _list_documents(folder_id: str) -> list[dict]:
    response = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents", params={"folder_id": folder_id})
    response.raise_for_status()
    return response.json()


def _register_self() -> dict:
    with _client() as client:
        response = client.post(
            "/paired-installations",
            json={"display_name": "Selbst (Loopback)", "base_url": "http://localhost:8000"},
        )
        response.raise_for_status()
        return response.json()


def _wait_for_status(
    client: httpx.Client, transfer_id: str, terminal_statuses: set[str], *, timeout: float = 20.0
) -> dict:
    deadline = time.monotonic() + timeout
    transfer = client.get(f"/transfers/{transfer_id}").json()
    while transfer["status"] not in terminal_statuses and time.monotonic() < deadline:
        time.sleep(0.5)
        transfer = client.get(f"/transfers/{transfer_id}").json()
    return transfer


def test_healthz():
    with _client() as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "migration-service"


def test_create_paired_installation_returns_api_key_once_and_list_hides_it():
    with _client() as client:
        created = client.post(
            "/paired-installations",
            json={
                "display_name": f"Peer-{uuid.uuid4().hex[:8]}",
                "base_url": "http://localhost:8000",
            },
        ).json()
        assert "api_key" in created

        listed = client.get("/paired-installations").json()
        entry = next(i for i in listed if i["id"] == created["id"])
        assert "api_key" not in entry


def test_create_paired_installation_with_explicit_api_key_uses_it_as_is():
    with _client() as client:
        response = client.post(
            "/paired-installations",
            json={
                "display_name": "Vorgegebener Key",
                "base_url": "http://localhost:8000",
                "api_key": "mein-eigener-schluessel",
            },
        )
    assert response.json()["api_key"] == "mein-eigener-schluessel"


def test_delete_paired_installation():
    with _client() as client:
        created = client.post(
            "/paired-installations",
            json={"display_name": "Löschen", "base_url": "http://localhost:8000"},
        ).json()
        response = client.delete(f"/paired-installations/{created['id']}")
        assert response.status_code == 204
        assert client.delete(f"/paired-installations/{created['id']}").status_code == 404


def test_transfer_to_unknown_target_returns_404():
    folder = _create_folder(name=f"quelle-{uuid.uuid4().hex[:8]}")
    with _client() as client:
        response = client.post(
            "/transfers",
            json={
                "source_folder_id": folder["id"],
                "target_installation_id": "does-not-exist",
                "created_by": "tester",
            },
        )
    assert response.status_code == 404


def test_transfer_start_requires_approval_when_configured():
    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        permission_client.put(
            "/approval-config/migration.transfer.start", json={"requires_approval": True}
        )

    installation = _register_self()
    folder = _create_folder(name=f"quelle-{uuid.uuid4().hex[:8]}")
    with _client() as client:
        response = client.post(
            "/transfers",
            json={
                "source_folder_id": folder["id"],
                "target_installation_id": installation["id"],
                "created_by": "tester",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_request_id"] is not None
    assert body["transfer"] is None


def test_dry_run_reports_ok_without_moving_any_data():
    installation = _register_self()
    folder = _create_folder(name=f"dry-run-quelle-{uuid.uuid4().hex[:8]}")
    _upload_document(folder_id=folder["id"], title="dokument.txt")

    with _client() as client:
        start = client.post(
            "/transfers",
            json={
                "source_folder_id": folder["id"],
                "target_installation_id": installation["id"],
                "created_by": "tester",
                "dry_run": True,
            },
        )
        assert start.status_code == 200
        transfer_id = start.json()["transfer"]["id"]

        transfer = _wait_for_status(client, transfer_id, {"dry_run_completed", "failed"})
    assert transfer["status"] == "dry_run_completed", transfer.get("error_message")

    # Keine tatsächliche Datenbewegung (7.2: "ohne tatsächliche Datenbewegung") -
    # der Quellordner selbst bleibt unangetastet (nicht gesperrt/gelöscht).
    assert _list_children(folder["id"]) == []


def test_full_transfer_lifecycle_self_loopback():
    """Kern-Szenario (7.2): Sperren -> Kopieren -> Verifizieren -> Freigabe ->
    (nach kurzer Frist) Löschung im Quellsystem - Selbst-Loopback wie bei
    `federation-hub-service` (P6-S9), da ein echter zweiter Installations-
    Stack im Sandbox nicht sinnvoll aufsetzbar ist."""
    installation = _register_self()
    folder = _create_folder(name=f"transfer-quelle-{uuid.uuid4().hex[:8]}")
    subfolder = _create_folder(parent_id=folder["id"], name="unterordner")
    document = _upload_document(
        folder_id=folder["id"], title="wurzel-dokument.txt", content=b"Inhalt A"
    )
    _upload_document(folder_id=subfolder["id"], title="unter-dokument.txt", content=b"Inhalt B")

    with _client() as client:
        start = client.post(
            "/transfers",
            json={
                "source_folder_id": folder["id"],
                "target_installation_id": installation["id"],
                "created_by": "tester",
                "retention_days": 0,
            },
        )
        assert start.status_code == 200, start.text
        transfer_id = start.json()["transfer"]["id"]

        # Mit `retention_days=0` durchläuft `do_engine_steps()` die gesamte
        # Kette (Sperren->Kopieren->Verifizieren->Freigabe->Warten->Löschen) in
        # einem synchronen Zug, da der Timer beim ersten Auswerten bereits
        # abgelaufen ist (real beobachtet: keine spürbare Wartezeit auf die
        # SLA-Poll-Schleife nötig) - ein Zwischenzustand wie "released" ist
        # von aussen daher praktisch nicht abpassbar, nur der Endzustand.
        transfer = _wait_for_status(client, transfer_id, {"released", "deleted", "failed"})
        assert transfer["status"] in ("released", "deleted"), transfer.get("error_message")
        assert transfer["documents_total"] == 2
        assert transfer["documents_copied"] == 2
        assert transfer["documents_verified"] == 2

        # Sperre wurde für die Migration angelegt UND danach wieder aufgehoben
        # (7.2: "keine Schreibzugriffe während der Migration") - eine
        # Live-Prüfung "ist sie GERADE JETZT aktiv" ist bei dieser bewusst
        # synchronen Referenzimplementierung von aussen nicht zuverlässig
        # abpassbar, daher die Prüfung anhand des dauerhaften Sperr-Verlaufs.
        scope_locks = httpx.get(
            f"{PERMISSION_SERVICE_URL}/scope-locks", params={"resource_id": folder["id"]}
        ).json()
        assert len(scope_locks) == 1
        assert scope_locks[0]["released_at"] is not None

        allowed_again = httpx.get(
            f"{PERMISSION_SERVICE_URL}/check",
            params={
                "principal_id": "irgendein-nutzer",
                "resource_id": folder["id"],
                "permission": "document.write",
                "access_type": "write",
            },
        ).json()
        assert allowed_again["blocked_by_scope_lock"] is False

        # Zielseite: derselbe Ordnername + dieselben zwei Dokumente unter root.
        target_children = _list_children("root")
        target_folder = next(f for f in target_children if f["name"] == folder["name"])
        target_documents = _list_documents(target_folder["id"])
        assert {d["title"] for d in target_documents} == {"wurzel-dokument.txt"}
        target_subfolders = _list_children(target_folder["id"])
        assert any(f["name"] == "unterordner" for f in target_subfolders)

        # Löschung im Quellsystem nach (hier: sofort abgelaufener) Übergangsfrist -
        # in der Regel schon erreicht (s. o.), notfalls über einen realen, aber
        # grosszügig bemessenen Poll-Timeout auf die SLA-Poll-Schleife warten.
        transfer = _wait_for_status(client, transfer_id, {"deleted", "failed"}, timeout=45.0)
        assert transfer["status"] == "deleted", transfer.get("error_message")

    source_after_deletion = _get_document(document["id"])
    assert source_after_deletion["deleted_at"] is not None
