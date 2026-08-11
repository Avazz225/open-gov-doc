import email.message
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from mail_connector.backends.interface import RawIncomingMessage
from mail_connector.main import _ingest_message, app

OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")

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
    session, *, uid: str, subject: str, body: str = "Hallo", attachment: bytes | None = None
):
    raw = RawIncomingMessage(
        uid=uid, raw_bytes=_build_raw_message(subject=subject, body=body, attachment=attachment)
    )
    await _ingest_message(session, raw)
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
    # Ein einzelner Bindestrich, damit der generische Kandidaten-Regex
    # (`matching._CANDIDATE_RE`) den gesamten Wert als EIN Token erkennt -
    # ein zusätzlicher Bindestrich würde stattdessen in zwei kürzere
    # Kandidaten zerfallen.
    unique_kennzeichen = f"2026-{uuid.uuid4().hex[:8]}"
    attributes = {**document["attributes"], "Kennzeichen": unique_kennzeichen}
    patch = httpx.patch(
        f"{DOCUMENT_SERVICE_URL}/documents/{document['id']}",
        json={"attributes": attributes},
        headers={"X-DMS-Roles": "dms-admin"},
        timeout=30.0,
    )
    patch.raise_for_status()
    return document["id"], unique_kennzeichen


async def _get_case(case_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as c:
        response = await c.get(f"http://localhost:8016/cases/{case_id}")
        response.raise_for_status()
        return response.json()


async def _get_case_documents(case_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as c:
        response = await c.get(f"http://localhost:8016/cases/{case_id}/documents")
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
