"""CMS signatures are recognised (P92-S4): libmagic calls DER PKCS#7
`application/octet-stream`. Pure, no DB."""

from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
from document_service.content_type_sniffer import (
    CMS_DETACHED,
    CMS_ENVELOPING,
    sniff_content_type,
)


def _signed(options: list) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    builder = (
        pkcs7.PKCS7SignatureBuilder().set_data(b"Inhalt").add_signer(cert, key, hashes.SHA256())
    )
    encoding = serialization.Encoding.PEM if "pem" in options else serialization.Encoding.DER
    return builder.sign(encoding, [o for o in options if o != "pem"])


def test_enveloping_and_detached_der():
    assert sniff_content_type(_signed([])) == CMS_ENVELOPING
    assert sniff_content_type(_signed([pkcs7.PKCS7Options.DetachedSignature])) == CMS_DETACHED


def test_pem_cms():
    assert sniff_content_type(_signed(["pem"])) == CMS_ENVELOPING


def test_other_data_is_left_to_libmagic():
    assert sniff_content_type(b"%PDF-1.4\n%%EOF") == "application/pdf"
    assert sniff_content_type(b"\x30\x03\x02\x01\x00") != CMS_ENVELOPING
    assert sniff_content_type(b"hallo") == "text/plain"


# --- `derivation_type` on upload (P92-S4) ----------------------------------

import pytest  # noqa: E402
from document_service.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "alice"}) as c:
        yield c


def _create(client, name, data, **form):
    return client.post(
        "/documents",
        data={"title": name, "created_by": "alice", **form},
        files={"file": (name, data, "application/octet-stream")},
    )


def test_upload_accepts_signature_and_signed_content_derivations(client):
    source = _create(client, "a.txt", b"Inhalt").json()
    detached = _signed([pkcs7.PKCS7Options.DetachedSignature])

    created = _create(
        client,
        "a.txt.p7s",
        detached,
        derived_from_document_id=source["id"],
        derived_from_version_number="1",
        derivation_type="signature",
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["derivation_type"] == "signature"
    assert body["derived_from_document_id"] == source["id"]
    version = client.get(f"/documents/{body['id']}/versions/1").json()
    assert version["content_type"] == CMS_DETACHED


def test_upload_refuses_other_or_unanchored_derivations(client):
    source = _create(client, "b.txt", b"Inhalt").json()
    redaction = _create(
        client,
        "x",
        b"x",
        derived_from_document_id=source["id"],
        derived_from_version_number="1",
        derivation_type="redaction",
    )
    assert redaction.status_code == 422
    unanchored = _create(client, "y", b"y", derivation_type="signature")
    assert unanchored.status_code == 422
