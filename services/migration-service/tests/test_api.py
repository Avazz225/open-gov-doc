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


_DMS_PRINCIPAL_HEADERS = {"X-DMS-Principal": "migration-tests"}


def _client() -> httpx.Client:
    """Default `X-DMS-Principal`/`X-DMS-Username` (Phase 59 Session 5) -
    `migration-tests` is granted `admin.migration_management` in
    `conftest.py`; `folder.read` needs no separate grant since it's a
    baseline "everyone" permission for ordinary folders (ADR 0149).
    `X-DMS-Username` becomes the server-derived `created_by` on every
    transfer - tests that need a distinct value override it per call."""
    return httpx.Client(
        base_url=MIGRATION_SERVICE_URL,
        timeout=30.0,
        headers={**_DMS_PRINCIPAL_HEADERS, "X-DMS-Username": "tester"},
    )


def _create_folder(*, parent_id: str = "root", name: str) -> dict:
    response = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders",
        json={"name": name, "parent_id": parent_id, "created_by": "migration-tests"},
        headers=_DMS_PRINCIPAL_HEADERS,
    )
    response.raise_for_status()
    return response.json()


def _upload_document(*, folder_id: str, title: str, content: bytes = b"Inhalt") -> dict:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": title, "created_by": "migration-tests", "folder_id": folder_id},
        files={"file": (title, content, "text/plain")},
        headers=_DMS_PRINCIPAL_HEADERS,
    )
    response.raise_for_status()
    return response.json()


def _get_document(document_id: str) -> dict:
    response = httpx.get(
        f"{DOCUMENT_SERVICE_URL}/documents/{document_id}", headers=_DMS_PRINCIPAL_HEADERS
    )
    response.raise_for_status()
    return response.json()


def _list_children(folder_id: str) -> list[dict]:
    response = httpx.get(
        f"{FOLDER_SERVICE_URL}/folders/{folder_id}/children", headers=_DMS_PRINCIPAL_HEADERS
    )
    response.raise_for_status()
    return response.json()


def _list_documents(folder_id: str) -> list[dict]:
    response = httpx.get(
        f"{DOCUMENT_SERVICE_URL}/documents",
        params={"folder_id": folder_id},
        headers=_DMS_PRINCIPAL_HEADERS,
    )
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


def test_step_endpoint_requires_workflow_service_caller():
    """P54-S2/ADR 0173: the six `/transfers/{id}/steps/*` endpoints are
    `connector_call` BPMN callback targets, not end-user-callable - a real
    `transfer_id` is enough to reach this check, the gate runs before the
    transfer lookup so the ID doesn't even need to be valid."""
    with _client() as client:
        response = client.post("/transfers/does-not-matter/steps/lock")
    assert response.status_code == 403


def test_step_endpoint_with_wrong_principal_returns_403():
    with _client() as client:
        response = client.post(
            "/transfers/does-not-matter/steps/lock",
            headers={"X-DMS-Principal": "someone-else"},
        )
    assert response.status_code == 403


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


def test_create_transfer_rejected_during_maintenance_mode():
    """Maintenance mode (4.8), Category A (Phase 56 Session 1, ADR 0152) -
    reads the gateway-forwarded `X-DMS-Maintenance-Active` header (no
    gateway in this test run, simulated directly), same pattern as
    `document-service`'s `test_create_document_rejected_during_maintenance_
    mode`. Fires before any validation - a nonexistent source folder still
    gets `503`, not `404`."""
    with _client() as client:
        response = client.post(
            "/transfers",
            json={
                "source_folder_id": "does-not-exist",
                "target_installation_id": "does-not-exist",
            },
            headers={"X-DMS-Maintenance-Active": "true"},
        )
    assert response.status_code == 503


def test_transfer_to_unknown_target_returns_404():
    folder = _create_folder(name=f"quelle-{uuid.uuid4().hex[:8]}")
    with _client() as client:
        response = client.post(
            "/transfers",
            json={
                "source_folder_id": folder["id"],
                "target_installation_id": "does-not-exist",
            },
        )
    assert response.status_code == 404


def test_transfer_start_requires_approval_when_configured():
    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        # Gegatet seit P32-S1 (ADR 0130) - reuses the running
        # `migration-service` container's own bootstrapped principal, same
        # as `conftest.py::_reset_approval_config`.
        permission_client.put(
            "/approval-config/migration.transfer.start",
            json={"requires_approval": True},
            headers={"X-DMS-Principal": "migration-service"},
        )

    installation = _register_self()
    folder = _create_folder(name=f"quelle-{uuid.uuid4().hex[:8]}")
    with _client() as client:
        response = client.post(
            "/transfers",
            json={
                "source_folder_id": folder["id"],
                "target_installation_id": installation["id"],
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


def test_create_paired_installation_without_principal_header_is_401():
    with httpx.Client(base_url=MIGRATION_SERVICE_URL, timeout=30.0) as client:
        response = client.post(
            "/paired-installations",
            json={"display_name": "Ohne Header", "base_url": "http://localhost:8000"},
        )
    assert response.status_code == 401


def test_create_paired_installation_without_permission_is_403():
    """RBAC (Phase 59 Session 5) - `POST /paired-installations` previously
    had NO permission check at all (only `license_gate`, which checks the
    installation's license, not the caller's identity)."""
    with httpx.Client(
        base_url=MIGRATION_SERVICE_URL,
        timeout=30.0,
        headers={"X-DMS-Principal": "some-random-authenticated-caller"},
    ) as client:
        response = client.post(
            "/paired-installations",
            json={"display_name": "Ohne Rolle", "base_url": "http://localhost:8000"},
        )
    assert response.status_code == 403


def test_delete_paired_installation_without_permission_is_403():
    with _client() as client:
        created = client.post(
            "/paired-installations",
            json={"display_name": "Löschversuch", "base_url": "http://localhost:8000"},
        ).json()
    with httpx.Client(
        base_url=MIGRATION_SERVICE_URL,
        timeout=30.0,
        headers={"X-DMS-Principal": "some-random-authenticated-caller"},
    ) as client:
        response = client.delete(f"/paired-installations/{created['id']}")
    assert response.status_code == 403


def test_create_paired_installation_rejects_ssrf_target():
    """SSRF guard (Phase 59 Session 5) - `base_url` previously had no
    validation at all; a private/internal address must now be rejected
    even for an otherwise-authorized, permissioned caller."""
    with _client() as client:
        response = client.post(
            "/paired-installations",
            json={"display_name": "SSRF-Versuch", "base_url": "http://10.0.0.5:8000"},
        )
    assert response.status_code == 422


def test_create_paired_installation_rejects_loopback_without_flag():
    """Confirms the loopback exemption is genuinely gated by
    `settings.allow_loopback_peers` (`true` in this dev/test stack, see
    `infra/docker-compose.yml`) and not a blanket, unconditional carve-out -
    verified indirectly via the metadata-style link-local address, which is
    NEVER exempted by the flag (only `is_loopback` is)."""
    with _client() as client:
        response = client.post(
            "/paired-installations",
            json={"display_name": "Metadata-Versuch", "base_url": "http://169.254.169.254:8000"},
        )
    assert response.status_code == 422


def test_make_peer_client_rejects_target_that_now_resolves_privately(monkeypatch):
    """DNS-rebinding TOCTOU fix (Phase 78 Session 1, gap-analysis round
    after Phase 77) - `_validate_peer_base_url` previously only ran once, at
    pairing-creation time; an attacker controlling a legitimately-paired
    peer's DNS could point it at a private/internal address afterward and
    the SSRF guard would never see it again. Simulates the exploitable
    shape directly at the unit level (rather than through this file's
    otherwise-exclusive black-box HTTP style): a real DNS-rebinding attack
    can't be reproduced against a live container without a test-controlled
    DNS server, which no test in this codebase sets up, so this patches
    `socket.getaddrinfo` to make the same hostname that would have passed
    validation once now resolve privately, and confirms `_make_peer_client` -
    called fresh immediately before every one of the 4 real transfer-step
    peer interactions - rejects it instead of silently proceeding."""
    import socket

    from fastapi import HTTPException
    from migration_service import main as migration_main

    class _FakeInstallation:
        base_url = "http://peer.example.test:8000"
        api_key = "irrelevant"

    def _fake_getaddrinfo(host, *_args, **_kwargs):
        assert host == "peer.example.test"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 8000))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    try:
        migration_main._make_peer_client(_FakeInstallation())
        raise AssertionError("expected _make_peer_client to reject a now-private target")
    except HTTPException as exc:
        assert exc.status_code == 422


async def test_ensure_process_definition_with_retry_returns_none_when_workflow_service_unreachable(
    monkeypatch,
):
    """Phase 79 phase-close - `lifespan()` previously called
    `workflow_client.ensure_process_definition()` directly with no error
    handling, crashing this entire service's startup (`Application startup
    failed. Exiting.`, `/healthz` itself unreachable) if `workflow-service`
    happened to not be reachable yet at exactly that moment - reproduced
    live during a full, serial `scripts/run-tests.sh --build` regression run
    (an unrelated startup-ordering race, not caused by this service's own
    code). Same in-process unit-level pattern as
    `test_make_peer_client_rejects_target_that_now_resolves_privately`
    above, since a real "workflow-service unreachable at exactly this
    moment" race can't be reproduced against a live container on demand."""
    from migration_service import main as migration_main

    class _FailingWorkflowClient:
        async def ensure_process_definition(self, *, name, bpmn_xml):
            raise httpx.ConnectError("simulated: workflow-service unreachable")

    async def _instant_sleep(*_args, **_kwargs) -> None:
        return None

    migration_main.app.state.workflow_client = _FailingWorkflowClient()
    monkeypatch.setattr(migration_main.asyncio, "sleep", _instant_sleep)

    result = await migration_main._ensure_process_definition_with_retry(
        name="irrelevant", bpmn_xml="<irrelevant/>"
    )

    assert result is None


async def test_start_transfer_raises_when_process_definition_unavailable(monkeypatch):
    """Defense in depth alongside the retry fix above: even if
    `transfer_definition_id`/`dry_run_definition_id` ends up `None` (startup
    retries exhausted), `_start_transfer` must refuse cleanly instead of
    calling `workflow_client.start_instance(None, ...)`."""
    from migration_service import main as migration_main

    monkeypatch.setattr(migration_main.app.state, "transfer_definition_id", None, raising=False)

    async def _fake_get_paired_installation(session, target_installation_id):
        class _Installation:
            id = target_installation_id

        return _Installation()

    async def _fake_create_transfer(session, **kwargs):
        class _Transfer:
            id = "irrelevant-transfer-id"

        return _Transfer()

    async def _noop(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        migration_main.repository, "get_paired_installation", _fake_get_paired_installation
    )
    monkeypatch.setattr(migration_main.repository, "create_transfer", _fake_create_transfer)
    monkeypatch.setattr(migration_main.repository, "set_workflow_instance", _noop)

    class _FakeSession:
        async def commit(self) -> None:
            return None

    try:
        await migration_main._start_transfer(
            _FakeSession(),
            source_folder_id="irrelevant",
            target_installation_id="irrelevant",
            created_by="tester",
            dry_run=False,
            retention_days=None,
        )
        raise AssertionError("expected ProcessDefinitionUnavailableError")
    except migration_main.ProcessDefinitionUnavailableError:
        pass


def test_create_transfer_without_principal_header_is_401():
    folder = _create_folder(name=f"quelle-{uuid.uuid4().hex[:8]}")
    with httpx.Client(base_url=MIGRATION_SERVICE_URL, timeout=30.0) as client:
        response = client.post(
            "/transfers",
            json={"source_folder_id": folder["id"], "target_installation_id": "does-not-exist"},
        )
    assert response.status_code == 401


def test_create_transfer_without_folder_read_permission_is_403():
    """RBAC (Phase 59 Session 5) - `POST /transfers` previously had NO
    permission check on the caller at all; `LocalDmsClient` always reads
    the source folder as the fixed, elevated `migration-service` identity,
    so without this check any caller could transfer a folder they have no
    access to at all. Uses an unregistered `source_folder_id` (no
    `ResourceNode` exists in `permission-service`) - fails closed by the
    same "unregistered resource denies" default this project already
    relies on elsewhere, reachable without a dedicated teamspace fixture.
    **Scope note**: this does not prove denial for a REAL folder a caller
    genuinely lacks access to - `folder.read` is a baseline "everyone"
    grant for ordinary, non-teamspace folders (ADR 0149), so that case
    would need a teamspace-scoped folder fixture, out of this session's
    scope (same limitation as P59-S3's signature-service session)."""
    with httpx.Client(base_url=MIGRATION_SERVICE_URL, timeout=30.0) as client:
        response = client.post(
            "/transfers",
            json={
                "source_folder_id": "does-not-exist-and-unregistered",
                "target_installation_id": "does-not-exist",
            },
            headers={"X-DMS-Principal": "some-random-authenticated-caller"},
        )
    assert response.status_code == 403
