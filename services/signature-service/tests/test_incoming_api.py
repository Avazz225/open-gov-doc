"""Incoming-signature validation end to end (P92-S1, ADR 0248): a signed PDF
uploaded to the real document-service produces `document.created`, the
in-process consumer validates it in the worker, and `GET /incoming-signatures`
shows the result. Also the trust-anchor and config endpoints."""

import os
import time

import httpx
import pytest
import testpki
from fastapi.testclient import TestClient
from signature_service.main import app

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
ADMIN = {"X-DMS-Principal": "signature-service-tests"}
with open(os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf"), "rb") as _f:
    PDF = _f.read()


@pytest.fixture
def client():
    with TestClient(app, headers=ADMIN) as c:
        yield c


def _upload(data: bytes, name: str = "eingang.pdf") -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": "Eingang signiert", "created_by": "alice"},
        files={"file": (name, data, "application/pdf")},
        headers={"X-DMS-Principal": "alice"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


def _add_version(document_id: str, base: int, data: bytes) -> None:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/versions",
        data={"expected_base_version_number": str(base)},
        files={"file": ("eingang-v2.pdf", data, "application/pdf")},
        headers={"X-DMS-Principal": "alice"},
        timeout=30,
    )
    response.raise_for_status()


def _wait_for(client: TestClient, document_id: str, version: int, timeout: float = 45) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/incoming-signatures", params={"document_id": document_id}).json()
        if any(s["version_number"] == version for s in body["summaries"]):
            return body
        time.sleep(0.3)
    raise AssertionError(f"no validation result for {document_id} v{version}")


def _anchor(client: TestClient, party: testpki.Party, label: str = "Partner-CA") -> dict:
    response = client.post(
        "/incoming-signatures/trust-anchors",
        json={"label": label, "certificate_pem": party.pem.decode()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_signed_upload_is_validated_and_a_later_change_fails(client):
    import asyncio

    root = testpki.make_cert("Partnerbehörde Root", ca=True)
    leaf = testpki.make_cert("Erika Mustermann", root, ca=False)
    signed = asyncio.run(testpki.sign_pdf(PDF, leaf, [root]))
    _anchor(client, root)

    document_id = _upload(signed)
    body = _wait_for(client, document_id, 1)

    [summary] = body["summaries"]
    assert summary["worst_indication"] == "TOTAL-PASSED"
    assert summary["best_qualification"] == "ades"
    assert summary["signature_count"] == 1
    assert summary["changed_after_signing"] is False
    [row] = body["validations"]
    assert row["trust_source"] == "installation"
    assert "Erika Mustermann" in row["signer_subject"]
    assert row["trigger"] == "event"
    assert row["revocation_mode"] == "soft-fail"

    # Version 2: the same signature with the page content swapped afterwards.
    _add_version(document_id, 1, testpki.replace_page_content(signed))
    body = _wait_for(client, document_id, 2)
    v2 = next(s for s in body["summaries"] if s["version_number"] == 2)
    assert v2["worst_indication"] == "TOTAL-FAILED"
    assert v2["changed_after_signing"] is True
    v2_row = next(r for r in body["validations"] if r["version_number"] == 2)
    assert v2_row["sub_indication"] == "MODIFIED_AFTER_SIGNING"
    assert v2_row["failure_reason"] == "Dokument wurde nach dem Signieren unzulässig verändert"


def test_unknown_ca_is_indeterminate_and_unsigned_pdfs_leave_no_result(client):
    import asyncio

    root = testpki.make_cert("Fremde CA", ca=True)
    leaf = testpki.make_cert("Unbekannt", root, ca=False)
    signed_id = _upload(asyncio.run(testpki.sign_pdf(PDF, leaf, [root])))
    unsigned_id = _upload(PDF, "ohne-signatur.pdf")

    body = _wait_for(client, signed_id, 1)
    assert body["summaries"][0]["worst_indication"] == "INDETERMINATE"
    assert body["validations"][0]["sub_indication"] == "NO_CERTIFICATE_CHAIN_FOUND"
    # Same queue, submitted earlier: by now the unsigned one is done too.
    time.sleep(1)
    unsigned = client.get("/incoming-signatures", params={"document_id": unsigned_id}).json()
    assert unsigned["summaries"] == [] and unsigned["validations"] == []


def test_own_signature_is_validated_against_the_internal_ca(client, pdf_document, real_signer):
    document_id, version = pdf_document
    signed = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "aes", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert signed.status_code == 201, signed.text
    new_version = signed.json()["version_number"]

    body = _wait_for(client, document_id, new_version)

    summary = next(s for s in body["summaries"] if s["version_number"] == new_version)
    assert summary["worst_indication"] == "TOTAL-PASSED"
    rows = [r for r in body["validations"] if r["version_number"] == new_version]
    signature = next(r for r in rows if r["kind"] == "signature")
    assert signature["trust_source"] == "internal"
    assert signature["best_signature_time"] is not None
    # The archive timestamp is listed, but isn't counted as a signature.
    assert any(r["kind"] == "document_timestamp" for r in rows)
    assert summary["signature_count"] == 1


def test_results_need_a_principal(client):
    response = client.get(
        "/incoming-signatures", params={"document_id": "x"}, headers={"X-DMS-Principal": ""}
    )
    assert response.status_code == 401


def test_disabled_validation_skips_new_versions(client):
    import asyncio

    config = client.get("/incoming-signatures/config").json()
    assert config["enabled"] is True and config["revocation_mode"] == "soft-fail"
    off = client.put(
        "/incoming-signatures/config",
        json={"enabled": False, "revocation_mode": "hard-fail", "outbound_fetching": False},
    )
    assert off.status_code == 200 and off.json()["updated_by"] == "signature-service-tests"
    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert("Signer", root, ca=False)
    document_id = _upload(asyncio.run(testpki.sign_pdf(PDF, leaf, [root])))
    with pytest.raises(AssertionError):
        _wait_for(client, document_id, 1, timeout=4)


def test_config_and_anchor_writes_need_the_signature_admin_role(client):
    root = testpki.make_cert("Root", ca=True)
    nobody = {"X-DMS-Principal": "someone-without-role"}
    assert (
        client.put(
            "/incoming-signatures/config",
            json={"enabled": True, "revocation_mode": "soft-fail", "outbound_fetching": True},
            headers=nobody,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/incoming-signatures/trust-anchors",
            json={"label": "x", "certificate_pem": root.pem.decode()},
            headers=nobody,
        ).status_code
        == 403
    )
    assert client.get("/incoming-signatures/trust-anchors", headers=nobody).status_code == 403


def test_trust_anchor_lifecycle(client):
    root = testpki.make_cert("Partner Root", ca=True)
    leaf = testpki.make_cert("Leaf", root, ca=False)

    listing = client.get("/incoming-signatures/trust-anchors").json()
    assert listing[0]["source"] == "internal" and listing[0]["id"] is None

    created = _anchor(client, root, "Partnerbehörde")
    assert created["source"] == "installation"
    assert "Partner Root" in created["subject"]

    duplicate = client.post(
        "/incoming-signatures/trust-anchors",
        json={"label": "nochmal", "certificate_pem": root.pem.decode()},
    )
    assert duplicate.status_code == 409
    not_ca = client.post(
        "/incoming-signatures/trust-anchors",
        json={"label": "Blatt", "certificate_pem": leaf.pem.decode()},
    )
    assert not_ca.status_code == 422
    garbage = client.post(
        "/incoming-signatures/trust-anchors",
        json={"label": "Müll", "certificate_pem": "-----BEGIN CERTIFICATE-----\nxx"},
    )
    assert garbage.status_code == 422

    assert [a["label"] for a in client.get("/incoming-signatures/trust-anchors").json()] == [
        "Interne Signatur-CA (OG Doc)",
        "Partnerbehörde",
    ]
    assert client.delete(f"/incoming-signatures/trust-anchors/{created['id']}").status_code == 204
    assert client.delete(f"/incoming-signatures/trust-anchors/{created['id']}").status_code == 404
    assert len(client.get("/incoming-signatures/trust-anchors").json()) == 1


def test_batch_summaries_for_list_badges(client):
    import asyncio

    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert("Signer", root, ca=False)
    signed_id = _upload(asyncio.run(testpki.sign_pdf(PDF, leaf, [root])))
    unsigned_id = _upload(PDF, "ohne.pdf")
    _wait_for(client, signed_id, 1)

    body = client.get(
        "/incoming-signatures/summaries",
        params={"document_ids": f"{signed_id},{unsigned_id},{signed_id}"},
    ).json()
    assert list(body) == [signed_id]
    assert body[signed_id][0]["worst_indication"] == "INDETERMINATE"

    assert client.get("/incoming-signatures/summaries", params={"document_ids": ""}).json() == {}
    too_many = ",".join(f"d{i}" for i in range(201))
    assert (
        client.get("/incoming-signatures/summaries", params={"document_ids": too_many}).status_code
        == 422
    )
    assert (
        client.get(
            "/incoming-signatures/summaries",
            params={"document_ids": signed_id},
            headers={"X-DMS-Principal": ""},
        ).status_code
        == 401
    )
