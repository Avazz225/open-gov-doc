"""CAdES end to end (P92-S4, ADR 0248): an enveloping `.p7m` uploaded to the
real document-service is validated and its content stored as a derived
document; a detached `.p7s` attached to a document shows on that document;
a PDF with its own PAdES signature plus an attached `.p7s` gets one combined
result."""

import asyncio
import os
import time

import httpx
import pytest
import testpki
from fastapi.testclient import TestClient
from signature_service.main import app

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
ADMIN = {"X-DMS-Principal": "signature-service-tests"}
ALICE = {"X-DMS-Principal": "alice"}
CONTENT = b"Bescheid vom 24.11.2026 - Inhalt, der signiert wurde.\n"
with open(os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf"), "rb") as _f:
    PDF = _f.read()


@pytest.fixture
def client():
    with TestClient(app, headers=ADMIN) as c:
        yield c


@pytest.fixture(scope="module")
def pki():
    root = testpki.make_cert("CAdES Partner Root", ca=True)
    return {"root": root, "leaf": testpki.make_cert("Max Mustermann", root, ca=False)}


def _anchor(client: TestClient, root: testpki.Party) -> None:
    response = client.post(
        "/incoming-signatures/trust-anchors",
        json={"label": "CAdES-Partner", "certificate_pem": root.pem.decode()},
    )
    assert response.status_code in (201, 409), response.text


def _upload(data: bytes, name: str, **form) -> dict:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": name, "created_by": "alice", **form},
        files={"file": (name, data, "application/octet-stream")},
        headers=ALICE,
        timeout=30,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait(client: TestClient, document_id: str, timeout: float = 45, runs: int = 1) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(
            "/incoming-signatures", params={"document_id": document_id, "history": True}
        ).json()
        if len({v["run_id"] for v in body["validations"]}) >= runs:
            return body
        time.sleep(0.3)
    raise AssertionError(f"no validation result for {document_id}")


def _derived(document_id: str) -> list[dict]:
    response = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/derived", headers=ALICE)
    response.raise_for_status()
    return response.json()


def test_enveloping_p7m_is_validated_and_its_content_extracted(client, pki):
    _anchor(client, pki["root"])
    p7m = testpki.cms_sign(CONTENT, [pki["leaf"]], [pki["root"]], detached=False)
    document = _upload(p7m, "bescheid.txt.p7m")
    assert document["current_version_number"] == 1

    body = _wait(client, document["id"])
    [row] = body["validations"]
    assert row["format"] == "cades" and row["position"] == "enveloping"
    assert row["indication"] == "TOTAL-PASSED"
    assert row["trust_source"] == "installation"

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not _derived(document["id"]):
        time.sleep(0.3)
    [extracted] = _derived(document["id"])
    assert extracted["derivation_type"] == "signed_content"
    assert extracted["title"] == "bescheid.txt (Inhalt von bescheid.txt.p7m)"
    content = httpx.get(
        f"{DOCUMENT_SERVICE_URL}/documents/{extracted['id']}/versions/1/content", headers=ALICE
    ).content
    assert content == CONTENT

    # Checking again doesn't extract a second time.
    assert (
        client.post(
            "/incoming-signatures/revalidate", json={"document_id": document["id"]}
        ).status_code
        == 202
    )
    _wait(client, document["id"], runs=2)
    assert len(_derived(document["id"])) == 1


def test_detached_p7s_shows_on_the_document_it_signs(client, pki):
    _anchor(client, pki["root"])
    signed = _upload(CONTENT, "anlage.txt")
    p7s = testpki.cms_sign(CONTENT, [pki["leaf"]], [pki["root"]], detached=True)
    signature = _upload(
        p7s,
        "anlage.txt.p7s",
        derived_from_document_id=signed["id"],
        derived_from_version_number="1",
        derivation_type="signature",
    )
    assert signature["derivation_type"] == "signature"

    body = _wait(client, signed["id"])
    [row] = body["validations"]
    assert row["format"] == "cades"
    assert row["position"] == "anlage.txt.p7s"
    assert row["indication"] == "TOTAL-PASSED"
    assert body["summaries"][0]["version_number"] == 1
    # Nothing is recorded on the signature file itself.
    own = client.get("/incoming-signatures", params={"document_id": signature["id"]}).json()
    assert own["summaries"] == []


def test_a_p7s_for_other_content_fails(client, pki):
    _anchor(client, pki["root"])
    signed = _upload(b"Ganz anderer Inhalt\n", "falsch.txt")
    p7s = testpki.cms_sign(CONTENT, [pki["leaf"]], [pki["root"]], detached=True)
    _upload(
        p7s,
        "falsch.txt.p7s",
        derived_from_document_id=signed["id"],
        derived_from_version_number="1",
        derivation_type="signature",
    )
    [row] = _wait(client, signed["id"])["validations"]
    assert row["indication"] == "TOTAL-FAILED"
    assert row["sub_indication"] == "HASH_FAILURE"


def test_pades_and_attached_p7s_share_one_result(client, pki):
    _anchor(client, pki["root"])
    signed_pdf = asyncio.run(testpki.sign_pdf(PDF, pki["leaf"], [pki["root"]]))
    pdf = _upload(signed_pdf, "vertrag.pdf")
    _wait(client, pdf["id"])  # the PAdES run from the upload event
    p7s = testpki.cms_sign(signed_pdf, [pki["leaf"]], [pki["root"]], detached=True)
    _upload(
        p7s,
        "vertrag.pdf.p7s",
        derived_from_document_id=pdf["id"],
        derived_from_version_number="1",
        derivation_type="signature",
    )
    body = _wait(client, pdf["id"], runs=2)
    latest = client.get("/incoming-signatures", params={"document_id": pdf["id"]}).json()
    assert sorted(v["format"] for v in latest["validations"]) == ["cades", "pades"]
    assert latest["summaries"][0]["signature_count"] == 2
    assert latest["summaries"][0]["worst_indication"] == "TOTAL-PASSED"
    assert len({v["run_id"] for v in body["validations"]}) == 2
