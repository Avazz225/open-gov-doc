import email.message
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from mail_connector import repository
from mail_connector.backends.interface import RawIncomingMessage
from mail_connector.main import _ingest_message, app, settings
from mail_connector.settings import MailboxConfig

OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
# "mailpit" (Docker-interner Hostname) löst vom Testlaufhost aus nicht auf -
# gleiche Überschreibung wie `DMS_SMTP_HOST` in conftest.py, hier für den
# direkten Zugriff auf mailpits eigene REST-API (Nachrichten-Verifikation).
MAILPIT_URL = os.environ.get("TEST_MAILPIT_URL", "http://localhost:8025")

ADMIN_HEADERS = {"X-DMS-Principal": "poststelle-1", "X-DMS-Roles": "dms-poststelle"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _build_raw_message(
    *, subject: str, body: str = "Sehr geehrte Damen und Herren.", attachment: bytes | None = None
) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = "buerger@example.com"
    msg["To"] = "posteingang@dms.local"
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename="anhang.pdf")
    return bytes(msg)


async def _ingest(
    session,
    *,
    uid: str,
    subject: str,
    body: str = "Hallo",
    attachment: bytes | None = None,
    mailbox_id: str = "central",
):
    raw = RawIncomingMessage(
        uid=uid, raw_bytes=_build_raw_message(subject=subject, body=body, attachment=attachment)
    )
    await _ingest_message(session, mailbox_id, raw)
    await session.commit()


def _real_document_with_kennzeichen() -> tuple[str, str]:
    """Legt einen echten Dokument-Objekttyp mit Kennzeichengenerator sowie
    ein Dokument dieses Typs an (P5e-S1) - liefert (document_id, kennzeichen)."""
    response = httpx.post(
        f"{OBJECT_TYPE_SERVICE_URL}/object-types",
        json={
            "name": f"mail-connector-test-type-{uuid.uuid4().hex[:8]}",
            "applies_to": "document",
            "kennzeichen_format": "{YYYY}-{Laufende_Nummer}",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    object_type_id = response.json()["id"]

    upload = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={
            "title": "Ausgangsdokument",
            "created_by": "alice",
            "object_type_id": str(object_type_id),
        },
        files={"file": ("dokument.txt", b"Inhalt", "text/plain")},
        timeout=30.0,
    )
    upload.raise_for_status()
    document = upload.json()

    # Der generierte Zähler startet bei jedem frisch angelegten Objekttyp
    # wieder bei 1 (P5e-S1) - über mehrere Testfälle hinweg würde das
    # wiederholt dasselbe "2026-001" liefern und eine absichtlich mehrdeutige
    # (weil objekttyp-übergreifend nicht eindeutige) Kennzeichen-Kollision
    # zwischen unabhängigen Tests erzeugen. Ein eindeutiger Testwert wird
    # stattdessen über die bereits bestehende admin-gegatete Kennzeichen-
    # Änderung gesetzt (siehe document-service's `test_update_kennzeichen_
    # with_admin_role_succeeds`).
    # Ein einzelner Bindestrich, damit der Kandidaten-Regex den gesamten Wert
    # als EIN Token erkennt - ein zusätzlicher Bindestrich würde stattdessen
    # in zwei kürzere Kandidaten zerfallen. Seit Post-Roadmap Phase 19
    # Session 11 wird das Kandidaten-Muster aus dem tatsächlich
    # konfigurierten `kennzeichen_format` abgeleitet (`{YYYY}-{Laufende_
    # Nummer}` hier) - `Laufende_Nummer` ist dabei stets rein numerisch
    # (`_render_kennzeichen`s `f"{n:03d}"`), daher rein numerische Ziffern
    # statt eines Hex-Suffixes für den Eindeutigkeits-Anteil.
    unique_kennzeichen = f"2026-{uuid.uuid4().int % 10**8:08d}"
    attributes = {**document["attributes"], "Kennzeichen": unique_kennzeichen}
    patch = httpx.patch(
        f"{DOCUMENT_SERVICE_URL}/documents/{document['id']}",
        json={"attributes": attributes},
        headers={"X-DMS-Roles": "dms-admin"},
        timeout=30.0,
    )
    patch.raise_for_status()
    return document["id"], unique_kennzeichen


# RBAC (P19-S5, ADR 0070) - case-service verlangt seither X-DMS-Principal;
# die "everyone"-Gruppe gewährt case.read per Default, ein beliebiger
# nicht-leerer Principal genügt.
_CASE_TEST_HEADERS = {"X-DMS-Principal": "mail-connector-tests"}


async def _get_case(case_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as c:
        response = await c.get(f"http://localhost:8016/cases/{case_id}", headers=_CASE_TEST_HEADERS)
        response.raise_for_status()
        return response.json()


async def _get_case_documents(case_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as c:
        response = await c.get(
            f"http://localhost:8016/cases/{case_id}/documents", headers=_CASE_TEST_HEADERS
        )
        response.raise_for_status()
        return response.json()


async def _get_document(document_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as c:
        response = await c.get(f"{DOCUMENT_SERVICE_URL}/documents/{document_id}")
        response.raise_for_status()
        return response.json()


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "mail-connector"


def test_list_inbound_requires_principal(client):
    response = client.get("/inbound")
    assert response.status_code == 401


def test_list_inbound_requires_poststelle_role(client):
    response = client.get(
        "/inbound", headers={"X-DMS-Principal": "someone", "X-DMS-Roles": "nothing-relevant"}
    )
    assert response.status_code == 403


async def test_ingest_without_match_stays_unassigned(client, session):
    await _ingest(session, uid="uid-1", subject="Allgemeine Anfrage", body="Kein Bezug erkennbar.")

    response = client.get("/inbound", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    [message] = [m for m in response.json() if m["from_address"] == "buerger@example.com"]
    assert message["status"] == "unassigned"
    assert message["proposed_target_id"] is None
    # Multi-Postfach-Modell (14.2, Post-Roadmap Phase 31 Session 12a) -
    # `_ingest` beansprucht standardmaessig das Standard-Postfach "central".
    assert message["mailbox_id"] == "central"
    # Textkörper zählt als eigener (synthetischer) Anhang.
    assert len(message["attachments"]) == 1
    assert message["attachments"][0]["scan_status"] == "clean"


async def test_ingest_with_attachment_scans_and_stores_both_parts(client, session):
    await _ingest(session, uid="uid-2", subject="Mit Anhang", attachment=b"%PDF-1.4 Testinhalt")

    response = client.get("/inbound", headers=ADMIN_HEADERS)
    [message] = [m for m in response.json() if m["subject"] == "Mit Anhang"]
    assert len(message["attachments"]) == 2
    filenames = {a["filename"] for a in message["attachments"]}
    assert "anhang.pdf" in filenames


async def test_ingest_detects_unique_kennzeichen_match(client, session):
    document_id, kennzeichen = _real_document_with_kennzeichen()

    await _ingest(session, uid="uid-3", subject=f"Rueckmeldung zu Az: {kennzeichen}")

    response = client.get(
        "/inbound", params={"status_filter": "proposed_match"}, headers=ADMIN_HEADERS
    )
    [message] = [m for m in response.json() if m["match_value"] == kennzeichen]
    assert message["match_type"] == "kennzeichen"
    assert message["proposed_target_type"] == "document"
    assert message["proposed_target_id"] == document_id


async def test_ingest_detects_unique_vorgangsnummer_match(client, session, real_case_id):
    case = await _get_case(real_case_id)
    vorgangsnummer = case["vorgangsnummer"]
    assert vorgangsnummer is not None

    await _ingest(session, uid="uid-4", subject=f"Vorgang {vorgangsnummer}")

    response = client.get("/inbound", headers=ADMIN_HEADERS)
    [message] = [m for m in response.json() if m["match_value"] == vorgangsnummer]
    assert message["match_type"] == "vorgangsnummer"
    assert message["proposed_target_type"] == "case"
    assert message["proposed_target_id"] == real_case_id


async def test_confirm_match_creates_document_in_matched_folder(client, session):
    document_id, kennzeichen = _real_document_with_kennzeichen()
    original = await _get_document(document_id)

    await _ingest(
        session, uid="uid-5", subject=f"Az: {kennzeichen}", body="Anbei die Rueckmeldung."
    )
    [message] = [
        m
        for m in client.get("/inbound", headers=ADMIN_HEADERS).json()
        if m["match_value"] == kennzeichen
    ]

    response = client.post(
        f"/inbound/{message['id']}/confirm-match",
        json={"title": "Rueckmeldung"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    [attachment] = body["attachments"]
    assert attachment["resulting_document_id"] is not None

    created = await _get_document(attachment["resulting_document_id"])
    assert created["folder_id"] == original["folder_id"]
    assert created["title"] == "Rueckmeldung"

    # Ein zweites Mal bestätigen ist nicht mehr möglich (409).
    again = client.post(
        f"/inbound/{message['id']}/confirm-match", json={"title": "Nochmal"}, headers=ADMIN_HEADERS
    )
    assert again.status_code == 409


async def test_confirm_match_without_folder_id_for_case_match_returns_400(
    client, session, real_case_id
):
    case = await _get_case(real_case_id)
    vorgangsnummer = case["vorgangsnummer"]

    await _ingest(session, uid="uid-6", subject=f"Vorgang {vorgangsnummer}")
    [message] = [
        m
        for m in client.get("/inbound", headers=ADMIN_HEADERS).json()
        if m["match_value"] == vorgangsnummer
    ]

    response = client.post(
        f"/inbound/{message['id']}/confirm-match",
        json={"title": "Rueckmeldung"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 400


async def test_assign_manually_creates_document_and_adds_case_reference(
    client, session, real_case_id
):
    await _ingest(session, uid="uid-7", subject="Ohne erkennbaren Bezug")
    [message] = [
        m
        for m in client.get("/inbound", headers=ADMIN_HEADERS).json()
        if m["subject"] == "Ohne erkennbaren Bezug"
    ]
    assert message["status"] == "unassigned"

    response = client.post(
        f"/inbound/{message['id']}/assign",
        json={"title": "Manuell zugeordnet", "folder_id": "root", "case_id": real_case_id},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"

    references = await _get_case_documents(real_case_id)
    assert len(references) == 1


async def test_reject_message(client, session):
    await _ingest(session, uid="uid-8", subject="Spam")
    [message] = [
        m for m in client.get("/inbound", headers=ADMIN_HEADERS).json() if m["subject"] == "Spam"
    ]

    response = client.post(
        f"/inbound/{message['id']}/reject", json={"reason": "Werbung"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["rejected_reason"] == "Werbung"


def test_outbound_requires_poststelle_role(client):
    response = client.post(
        "/outbound",
        json={"to_address": "extern@example.com", "subject": "Antwort", "body": "Hallo"},
        headers={"X-DMS-Principal": "someone", "X-DMS-Roles": "nothing-relevant"},
    )
    assert response.status_code == 403


def test_send_outbound_and_list(client):
    response = client.post(
        "/outbound",
        json={
            "to_address": "extern@example.com",
            "subject": "Antwort auf Ihre Anfrage",
            "body": "Vielen Dank fuer Ihre Nachricht.",
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "sent"
    assert body["error_message"] is None
    # sent_by kommt aus X-DMS-Principal, nicht aus dem Body (keine
    # client-gefälschte Urheberangabe möglich).
    assert body["sent_by"] == "poststelle-1"

    listed = client.get("/outbound", headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    assert any(m["to_address"] == "extern@example.com" for m in listed.json())


def _upload_test_document(*, filename: str, content_type: str, data: bytes) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": "Anhang-Testdokument", "created_by": "alice"},
        files={"file": (filename, data, content_type)},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def _find_mailpit_message_by_subject(subject: str) -> dict:
    response = httpx.get(f"{MAILPIT_URL}/api/v1/search", params={"query": subject}, timeout=30.0)
    response.raise_for_status()
    [summary] = response.json()["messages"]
    detail = httpx.get(f"{MAILPIT_URL}/api/v1/message/{summary['ID']}", timeout=30.0)
    detail.raise_for_status()
    return detail.json()


def test_send_outbound_with_related_document_attaches_file(client):
    filename = f"anhang-{uuid.uuid4().hex[:8]}.txt"
    content = b"Inhalt des verknuepften Dokuments fuer den Postausgang-Anhang-Test."
    document_id = _upload_test_document(filename=filename, content_type="text/plain", data=content)
    subject = f"Antwort mit Anhang {uuid.uuid4().hex[:8]}"

    try:
        response = client.post(
            "/outbound",
            json={
                "to_address": "extern@example.com",
                "subject": subject,
                "body": "Anbei das angeforderte Dokument.",
                "related_document_id": document_id,
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "sent"
        assert body["related_document_id"] == document_id

        message = _find_mailpit_message_by_subject(subject)
        [attachment] = message["Attachments"]
        assert attachment["FileName"] == filename
        assert attachment["ContentType"] == "text/plain"
        assert attachment["Size"] == len(content)

        part = httpx.get(
            f"{MAILPIT_URL}/api/v1/message/{message['ID']}/part/{attachment['PartID']}",
            timeout=30.0,
        )
        part.raise_for_status()
        assert part.content == content
    finally:
        httpx.delete(f"{DOCUMENT_SERVICE_URL}/documents/{document_id}", timeout=30.0)


def test_send_outbound_with_unknown_related_document_returns_400(client):
    response = client.post(
        "/outbound",
        json={
            "to_address": "extern@example.com",
            "subject": "Sollte nicht versendet werden",
            "body": "Hallo",
            "related_document_id": "does-not-exist",
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    # Kein Datensatz angelegt (Eingabefehler VOR dem Versandversuch geprüft,
    # siehe `_attach_related_document`s Aufrufstelle in `send_outbound`).
    listed = client.get("/outbound", headers=ADMIN_HEADERS)
    assert not any(m["subject"] == "Sollte nicht versendet werden" for m in listed.json())


# --- Multi-Postfach-Modell (14.2, Post-Roadmap Phase 31 Session 12a) -------


def test_list_mailboxes_requires_principal(client):
    response = client.get("/mailboxes")
    assert response.status_code == 401


def test_list_mailboxes_requires_poststelle_role(client):
    response = client.get(
        "/mailboxes", headers={"X-DMS-Principal": "someone", "X-DMS-Roles": "nothing-relevant"}
    )
    assert response.status_code == 403


def test_list_mailboxes_returns_configured_mailboxes_without_credentials(client):
    response = client.get("/mailboxes", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    [mailbox] = response.json()
    assert mailbox == {
        "id": "central",
        "name": "Zentrale Poststelle",
        "kind": "central",
        "owning_group_id": None,
    }
    # Keine Zugangsdaten in der Antwort (ADR 0091-Praezedenzfall - Secrets
    # bleiben env-var-only, niemals in einer GET-Antwort).
    assert "pop3_password" not in mailbox
    assert "pop3_username" not in mailbox


async def test_list_inbound_filters_by_mailbox_id(client, session):
    await _ingest(session, uid="uid-mailbox-filter", subject="Fuer das Standard-Postfach")

    matching = client.get(
        "/inbound", params={"mailbox_id": "central"}, headers=ADMIN_HEADERS
    ).json()
    other = client.get(
        "/inbound", params={"mailbox_id": "does-not-exist"}, headers=ADMIN_HEADERS
    ).json()

    assert any(m["subject"] == "Fuer das Standard-Postfach" for m in matching)
    assert not any(m["subject"] == "Fuer das Standard-Postfach" for m in other)


# --- Weiterleitung zwischen Postfächern / "Postbuch"-Grundlage (14.2,
# Post-Roadmap Phase 31 Session 12b) ----------------------------------------


@pytest.fixture
def with_finanzen_mailbox(monkeypatch):
    """Fuegt fuer die Dauer eines Tests ein zweites, konfiguriertes Postfach
    hinzu (Settings sind sonst nur mit dem Standard-Postfach "central"
    bestueckt) - echte Weiterleitung zwischen zwei Postfaechern ist ohne
    ein zweites konfiguriertes Ziel nicht sinnvoll testbar."""
    monkeypatch.setattr(
        settings,
        "mailboxes",
        [
            *settings.mailboxes,
            MailboxConfig(
                id="finanzen",
                name="Poststelle Finanzen",
                kind="departmental",
                owning_group_id="group-finanzen",
                inbound_protocol="pop3",
                pop3_host="irrelevant",
                pop3_username="irrelevant",
                pop3_password="irrelevant",
            ),
        ],
    )


def test_route_message_requires_principal(client):
    response = client.post("/inbound/does-not-exist/route", json={"target_mailbox_id": "finanzen"})
    assert response.status_code == 401


def test_route_unknown_message_returns_404(client, with_finanzen_mailbox):
    response = client.post(
        "/inbound/does-not-exist/route",
        json={"target_mailbox_id": "finanzen"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 404


async def test_route_message_to_unconfigured_mailbox_returns_422(client, session):
    await _ingest(session, uid="uid-route-1", subject="Weiterleitung Ziel unbekannt")
    [message] = [
        m
        for m in client.get("/inbound", headers=ADMIN_HEADERS).json()
        if m["subject"] == "Weiterleitung Ziel unbekannt"
    ]

    response = client.post(
        f"/inbound/{message['id']}/route",
        json={"target_mailbox_id": "does-not-exist"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422


async def test_route_message_to_same_mailbox_returns_422(client, session, with_finanzen_mailbox):
    await _ingest(session, uid="uid-route-2", subject="Weiterleitung dasselbe Postfach")
    [message] = [
        m
        for m in client.get("/inbound", headers=ADMIN_HEADERS).json()
        if m["subject"] == "Weiterleitung dasselbe Postfach"
    ]

    response = client.post(
        f"/inbound/{message['id']}/route",
        json={"target_mailbox_id": "central"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422


async def test_route_message_updates_mailbox_and_records_history(
    client, session, with_finanzen_mailbox
):
    await _ingest(session, uid="uid-route-3", subject="Weiterleitung Erfolg")
    [message] = [
        m
        for m in client.get("/inbound", headers=ADMIN_HEADERS).json()
        if m["subject"] == "Weiterleitung Erfolg"
    ]
    assert message["mailbox_id"] == "central"
    assert message["routing_log"] == []

    response = client.post(
        f"/inbound/{message['id']}/route",
        json={"target_mailbox_id": "finanzen", "reason": "Fachbezug Finanzen"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mailbox_id"] == "finanzen"
    [entry] = body["routing_log"]
    assert entry["from_mailbox_id"] == "central"
    assert entry["to_mailbox_id"] == "finanzen"
    assert entry["routed_by"] == "poststelle-1"
    assert entry["reason"] == "Fachbezug Finanzen"

    # Erscheint jetzt beim Ziel-Postfach, nicht mehr beim Ursprungs-Postfach.
    finanzen = client.get(
        "/inbound", params={"mailbox_id": "finanzen"}, headers=ADMIN_HEADERS
    ).json()
    central = client.get("/inbound", params={"mailbox_id": "central"}, headers=ADMIN_HEADERS).json()
    assert any(m["id"] == message["id"] for m in finanzen)
    assert not any(m["id"] == message["id"] for m in central)


async def test_route_already_confirmed_message_returns_409(client, session, with_finanzen_mailbox):
    document_id, kennzeichen = _real_document_with_kennzeichen()
    await _ingest(session, uid="uid-route-4", subject=f"Az: {kennzeichen}")
    [message] = [
        m
        for m in client.get("/inbound", headers=ADMIN_HEADERS).json()
        if m["match_value"] == kennzeichen
    ]
    client.post(
        f"/inbound/{message['id']}/confirm-match",
        json={"title": "Erledigt"},
        headers=ADMIN_HEADERS,
    )

    response = client.post(
        f"/inbound/{message['id']}/route",
        json={"target_mailbox_id": "finanzen"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 409


async def test_route_message_to_mailbox_with_colliding_source_uid_returns_409(
    client, session, with_finanzen_mailbox
):
    """Found live during P31-S12b verification (see `DuplicateInTargetMailboxError`
    in `repository.py`): two mailboxes independently polling the same physical
    mail account can each ingest their own copy of a message sharing the same
    backend-native UID - routing must surface a clean 409, not a raw 500 from
    the composite unique constraint. The "finanzen" duplicate is created
    directly via `repository.create_inbound_message` (bypassing the virus-scan/
    event-publish pipeline `_ingest` goes through) - only the "central" message
    actually being routed needs the full pipeline, since it's the one fetched
    and posted to through `client` below."""
    await _ingest(
        session, uid="uid-route-collision", subject="Zentrale Kopie", mailbox_id="central"
    )
    await repository.create_inbound_message(
        session,
        mailbox_id="finanzen",
        source_uid="uid-route-collision",
        from_address="buerger@example.com",
        subject="Finanzen Kopie",
        body_text="Hallo",
        received_at=datetime.now(UTC),
        match_type=None,
        match_value=None,
        proposed_target_type=None,
        proposed_target_id=None,
        match_candidates=[],
    )
    await session.commit()
    central_messages = client.get(
        "/inbound", params={"mailbox_id": "central"}, headers=ADMIN_HEADERS
    ).json()
    [message] = [m for m in central_messages if m["subject"] == "Zentrale Kopie"]

    response = client.post(
        f"/inbound/{message['id']}/route",
        json={"target_mailbox_id": "finanzen"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 409
    unchanged = client.get(f"/inbound/{message['id']}", headers=ADMIN_HEADERS).json()
    assert unchanged["mailbox_id"] == "central"
    assert unchanged["routing_log"] == []


# --- Durchsuchbares "Postbuch"-Register (14.2, Post-Roadmap Phase 31 Session
# 12c) - ueber alle Nachrichten hinweg, im Unterschied zum je-Nachricht
# eingebetteten `routing_log` aus P31-S12b -------------------------------


def test_search_routing_log_requires_principal(client):
    response = client.get("/routing-log")
    assert response.status_code == 401


async def test_search_routing_log_returns_hop_with_message_context(
    client, session, with_finanzen_mailbox
):
    await _ingest(session, uid="uid-postbuch-1", subject="Postbuch Testeintrag")
    [message] = [
        m
        for m in client.get("/inbound", headers=ADMIN_HEADERS).json()
        if m["subject"] == "Postbuch Testeintrag"
    ]
    client.post(
        f"/inbound/{message['id']}/route",
        json={"target_mailbox_id": "finanzen", "reason": "Fachbezug"},
        headers=ADMIN_HEADERS,
    )

    response = client.get("/routing-log", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    [entry] = [e for e in response.json() if e["message_id"] == message["id"]]
    assert entry["from_mailbox_id"] == "central"
    assert entry["to_mailbox_id"] == "finanzen"
    assert entry["routed_by"] == "poststelle-1"
    assert entry["reason"] == "Fachbezug"
    assert entry["message_subject"] == "Postbuch Testeintrag"
    assert entry["message_current_mailbox_id"] == "finanzen"
    assert entry["message_status"] == "unassigned"


async def test_search_routing_log_filters_by_mailbox_id_and_q(
    client, session, with_finanzen_mailbox
):
    """Seeds both messages directly via `repository.create_inbound_message`
    (bypassing the virus-scan/event-publish `_ingest` pipeline, same reason
    as `test_route_message_to_mailbox_with_colliding_source_uid_returns_409`
    - this test's own subject is `GET /routing-log`'s filtering, not
    ingestion, and two back-to-back `_ingest` calls would double this test's
    exposure to the pre-existing event-loop flakiness documented elsewhere
    in this file)."""
    alpha = await repository.create_inbound_message(
        session,
        mailbox_id="central",
        source_uid="uid-postbuch-2",
        from_address="buerger@example.com",
        subject="Postbuch Filtertest Alpha",
        body_text="Hallo",
        received_at=datetime.now(UTC),
        match_type=None,
        match_value=None,
        proposed_target_type=None,
        proposed_target_id=None,
        match_candidates=[],
    )
    beta = await repository.create_inbound_message(
        session,
        mailbox_id="central",
        source_uid="uid-postbuch-3",
        from_address="buerger@example.com",
        subject="Postbuch Filtertest Beta",
        body_text="Hallo",
        received_at=datetime.now(UTC),
        match_type=None,
        match_value=None,
        proposed_target_type=None,
        proposed_target_id=None,
        match_candidates=[],
    )
    await session.commit()
    client.post(
        f"/inbound/{alpha.id}/route",
        json={"target_mailbox_id": "finanzen"},
        headers=ADMIN_HEADERS,
    )
    client.post(
        f"/inbound/{beta.id}/route",
        json={"target_mailbox_id": "finanzen"},
        headers=ADMIN_HEADERS,
    )

    by_q = client.get("/routing-log", params={"q": "Alpha"}, headers=ADMIN_HEADERS).json()
    by_mailbox = client.get(
        "/routing-log", params={"mailbox_id": "finanzen"}, headers=ADMIN_HEADERS
    ).json()
    by_unrelated_mailbox = client.get(
        "/routing-log", params={"mailbox_id": "does-not-exist"}, headers=ADMIN_HEADERS
    ).json()

    assert [e["message_id"] for e in by_q] == [alpha.id]
    assert {e["message_id"] for e in by_mailbox} >= {alpha.id, beta.id}
    assert by_unrelated_mailbox == []
