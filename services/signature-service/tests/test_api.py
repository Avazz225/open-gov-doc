import pytest
from fastapi.testclient import TestClient
from signature_service.main import app

# Muss mit conftest.py::SIGNATURE_CONFIG_ADMIN_PRINCIPAL_ID übereinstimmen
# (dort per `_grant_signature_config_permission`-Fixture berechtigt) - kein
# Cross-File-Import von Test-Konstanten, gleiche Projektkonvention wie
# andernorts.
SIGNATURE_CONFIG_ADMIN_HEADERS = {"X-DMS-Principal": "signature-service-tests"}


@pytest.fixture
def client():
    """Default `X-DMS-Principal` (Phase 59 Session 3) - `document.write`/
    `.read` are baseline "everyone" grants (ADR 0149), so any non-empty,
    authenticated principal already passes the new permission checks on an
    ordinary (non-teamspace) document; reuses the SAME literal string
    `pdf_document`/`non_pdf_document` (conftest.py) already use to create
    the test document against the real `document-service`, for one
    consistent test identity rather than a second synthetic one. Does NOT
    set a default `X-DMS-Username` - `real_signer` (conftest.py) is
    generated fresh per test, so `POST /signatures` calls pass it
    explicitly per call instead."""
    with TestClient(app, headers={"X-DMS-Principal": "signature-service-tests"}) as c:
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "signature-service"


def test_create_signature_rejected_during_maintenance_mode(client):
    """Maintenance mode (4.8), Category A (Phase 56 Session 1, ADR 0152) -
    reads the gateway-forwarded `X-DMS-Maintenance-Active` header (no
    gateway in this test run, simulated directly), same pattern as
    `document-service`'s `test_create_document_rejected_during_maintenance_
    mode`. Fires before the `document_client.get_document` lookup - an
    unknown `document_id` still gets `503`, not `404`."""
    response = client.post(
        "/signatures",
        json={"document_id": "does-not-exist", "level": "ses", "signer_principal_id": "alice"},
        headers={"X-DMS-Maintenance-Active": "true"},
    )
    assert response.status_code == 503


def test_sign_ses_with_real_signer_creates_new_document_version_and_verifies(
    client, pdf_document, real_signer
):
    document_id, source_version = pdf_document

    response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["level"] == "ses"
    assert body["connector_id"] == "internal"
    assert body["source_version_number"] == source_version
    assert body["version_number"] == source_version + 1
    assert "DMS System (SES)" in body["certificate_subject"]

    verify_response = client.get(f"/signatures/{body['id']}/verify")
    assert verify_response.status_code == 200
    verification = verify_response.json()
    assert verification["valid"] is True
    assert verification["integrity_intact"] is True
    assert verification["certificate_expired"] is False
    assert verification["errors"] == []


def test_sign_aes_binds_personal_certificate(client, pdf_document, real_signer):
    document_id, _source_version = pdf_document

    response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "aes", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["level"] == "aes"
    assert "Sig Test" in body["certificate_subject"]

    verify_response = client.get(f"/signatures/{body['id']}/verify")
    assert verify_response.json()["valid"] is True


def test_sign_rejects_non_pdf_document(client, non_pdf_document, real_signer):
    document_id, _version = non_pdf_document
    response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert response.status_code == 400


def test_sign_rejects_unknown_document(client, real_signer):
    response = client.post(
        "/signatures",
        json={
            "document_id": "does-not-exist",
            "level": "ses",
            "signer_principal_id": real_signer,
        },
    )
    assert response.status_code == 404


def test_sign_rejects_unknown_signer(client, pdf_document):
    document_id, _version = pdf_document
    response = client.post(
        "/signatures",
        json={
            "document_id": document_id,
            "level": "ses",
            "signer_principal_id": "does-not-exist-principal",
        },
        headers={"X-DMS-Username": "does-not-exist-principal"},
    )
    assert response.status_code == 400


def test_sign_rejects_qes_when_no_connector_configured(client, pdf_document, real_signer):
    document_id, _version = pdf_document
    response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "qes", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert response.status_code == 400


def test_get_signature_config_returns_env_defaults(client):
    """Post-Roadmap Phase 22 Session 6, ADR 0091 - vor dem ersten `PUT`
    spiegelt `GET /signature-config` den bisherigen Env-Var-Ausgangswert
    (`Settings.signature_providers`), kein separates Seed-Skript nötig."""
    response = client.get("/signature-config")
    assert response.status_code == 200
    assert response.json() == [{"id": "internal", "type": "internal", "levels": ["ses", "aes"]}]


def test_put_signature_config_rejects_unknown_provider(client):
    """'Nur bestehende Einträge bearbeiten' (Sessionsvorgabe) - eine
    unbekannte Connector-`id` wird abgelehnt statt sie stillschweigend
    anzulegen."""
    response = client.put(
        "/signature-config",
        json=[{"id": "does-not-exist", "levels": ["ses"]}],
        headers=SIGNATURE_CONFIG_ADMIN_HEADERS,
    )
    assert response.status_code == 422


def test_put_signature_config_rejects_empty_levels(client):
    response = client.put(
        "/signature-config",
        json=[{"id": "internal", "levels": []}],
        headers=SIGNATURE_CONFIG_ADMIN_HEADERS,
    )
    assert response.status_code == 422


def test_put_signature_config_rejects_qes_for_internal_type(client):
    """Dieselbe Validierung wie `SignatureProviderConfig._check_levels`
    (Settings-Schema) - `type=internal` kann kein QES ausstellen, jetzt zur
    Laufzeit statt nur beim Start geprüft."""
    response = client.put(
        "/signature-config",
        json=[{"id": "internal", "levels": ["qes"]}],
        headers=SIGNATURE_CONFIG_ADMIN_HEADERS,
    )
    assert response.status_code == 422


def test_put_signature_config_without_principal_header_is_401(client):
    response = client.put(
        "/signature-config",
        json=[{"id": "internal", "levels": ["ses"]}],
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_put_signature_config_without_permission_is_403(client):
    response = client.put(
        "/signature-config",
        json=[{"id": "internal", "levels": ["ses"]}],
        headers={"X-DMS-Principal": "some-random-authenticated-caller"},
    )
    assert response.status_code == 403


def test_put_signature_config_takes_effect_without_restart(client, pdf_document, real_signer):
    """Kernverhalten dieser Session (Live-Reload, kein `app.state`-Cache):
    nach `PUT /signature-config` mit `levels=["ses"]` (AES entfernt) schlägt
    ein Signieren mit `level="aes"` sofort fehl - ganz ohne Neustart
    zwischen PUT und dem nachfolgenden Signaturversuch."""
    document_id, _version = pdf_document

    put_response = client.put(
        "/signature-config",
        json=[{"id": "internal", "levels": ["ses"]}],
        headers=SIGNATURE_CONFIG_ADMIN_HEADERS,
    )
    assert put_response.status_code == 200
    assert put_response.json() == [{"id": "internal", "type": "internal", "levels": ["ses"]}]

    aes_response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "aes", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert aes_response.status_code == 400

    ses_response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert ses_response.status_code == 201


def test_sign_respects_object_type_minimum_level(
    client, pdf_document_with_required_level, real_signer
):
    document_id, _version = pdf_document_with_required_level

    ses_response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert ses_response.status_code == 400

    aes_response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "aes", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    )
    assert aes_response.status_code == 201


def test_list_and_get_signature(client, pdf_document, real_signer):
    document_id, _version = pdf_document
    created = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    ).json()

    get_response = client.get(f"/signatures/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["document_id"] == document_id

    list_response = client.get("/signatures", params={"document_id": document_id})
    assert created["id"] in {s["id"] for s in list_response.json()}


def test_get_unknown_signature_returns_404(client):
    response = client.get("/signatures/999999")
    assert response.status_code == 404


def test_verify_unknown_signature_returns_404(client):
    response = client.get("/signatures/999999/verify")
    assert response.status_code == 404


def test_create_signature_without_principal_header_is_401(client, pdf_document, real_signer):
    """RBAC (Phase 59 Session 3) - `POST /signatures` previously had no
    caller-permission check at all; this and the next test prove the new
    gate actually fires."""
    document_id, _version = pdf_document
    response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Principal": "", "X-DMS-Username": real_signer},
    )
    assert response.status_code == 401


def test_create_signature_with_mismatched_signer_is_403(client, pdf_document, real_signer):
    """`signer_principal_id` must match the caller's own, gateway-verified
    `X-DMS-Username` (Phase 59 Session 3) - previously fully
    client-supplied and spoofable, letting any caller sign in someone
    else's name."""
    document_id, _version = pdf_document
    response = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": "someone-else"},
    )
    assert response.status_code == 403


def test_list_signatures_without_document_id_is_400(client):
    """No more system-wide listing (Phase 59 Session 3) - `document_id` is
    now mandatory."""
    response = client.get("/signatures")
    assert response.status_code == 400


def test_list_signatures_without_principal_header_is_401(client, pdf_document):
    document_id, _version = pdf_document
    response = client.get(
        "/signatures", params={"document_id": document_id}, headers={"X-DMS-Principal": ""}
    )
    assert response.status_code == 401


def test_get_signature_without_principal_header_is_401(client, pdf_document, real_signer):
    document_id, _version = pdf_document
    created = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    ).json()

    response = client.get(f"/signatures/{created['id']}", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_verify_signature_without_principal_header_is_401(client, pdf_document, real_signer):
    document_id, _version = pdf_document
    created = client.post(
        "/signatures",
        json={"document_id": document_id, "level": "ses", "signer_principal_id": real_signer},
        headers={"X-DMS-Username": real_signer},
    ).json()

    response = client.get(f"/signatures/{created['id']}/verify", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401
