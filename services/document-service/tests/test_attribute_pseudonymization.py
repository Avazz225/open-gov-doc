import uuid

import httpx
import pytest
from document_service.main import app
from document_service.settings import Settings
from fastapi.testclient import TestClient

settings = Settings()

# Deliberately self-contained (no cross-file import from conftest.py's
# constants) - same project convention as test_redaction.py/test_export.py.
# Principal ids must still match conftest.py's grant fixtures exactly.
PSEUDONYMIZATION_ADMIN_HEADERS = {"X-DMS-Principal": "document-service-test-pseudonymization-admin"}
REVEAL_ADMIN_HEADERS = {"X-DMS-Principal": "document-service-test-pii-reveal-admin"}
OBJECT_CONFIG_ADMIN_HEADERS = {"X-DMS-Principal": "document-service-test-object-config-admin"}


@pytest.fixture
def client():
    """Post-Roadmap Phase 38 Session 4 (ADR 0149): default `X-DMS-Principal`,
    same pattern as `test_api.py`'s `client` fixture."""
    with TestClient(app, headers={"X-DMS-Principal": "document-service-tests"}) as c:
        yield c


@pytest.fixture
def object_type_id():
    """One attribute marked `personal_data: true` (SVNR), one not
    (Betreff) - `personal_data` is a free-form key inside the already
    schema-free `attributes` list (`dms_constraint_engine` only reads keys
    it recognizes, an unrecognized one is simply ignored at validation
    time), so no object-type-service code change was needed for this."""
    with httpx.Client(
        base_url=settings.object_type_service_base_url,
        timeout=10.0,
        headers=OBJECT_CONFIG_ADMIN_HEADERS,
    ) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": f"pseudonym-test-type-{uuid.uuid4().hex[:8]}",
                "applies_to": "document",
                "attributes": [
                    {"name": "SVNR", "type": "string", "personal_data": True},
                    {"name": "Betreff", "type": "string"},
                ],
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


@pytest.fixture
async def poll_object_type_client():
    """A test-local `ObjectTypeClient`, separate from `app.state`'s own
    (which is bound to `TestClient`'s internal event loop, not a plain
    `async def` test function's - see `_pseudonymize_eligible_attributes`'s
    docstring)."""
    from document_service.object_type_client import ObjectTypeClient

    oc = ObjectTypeClient(settings.object_type_service_base_url)
    yield oc
    await oc.close()


@pytest.fixture
def document_id(client, object_type_id):
    response = client.post(
        "/documents",
        data={
            "title": "Testfall",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": '{"SVNR": "123-45-6789", "Betreff": "Testfall"}',
        },
        files={"file": ("x.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    response.raise_for_status()
    return response.json()["id"]


def _pseudonymize(client, document_id, attribute_name="SVNR", **overrides):
    payload = {"pseudonymized_by": "carol", **overrides}
    return client.post(
        f"/documents/{document_id}/attributes/{attribute_name}/pseudonymize",
        json=payload,
        headers=PSEUDONYMIZATION_ADMIN_HEADERS,
    )


def test_pseudonymize_requires_principal_header(client, document_id):
    response = client.post(
        f"/documents/{document_id}/attributes/SVNR/pseudonymize",
        json={"pseudonymized_by": "carol"},
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_pseudonymize_requires_pseudonymization_permission(client, document_id):
    response = client.post(
        f"/documents/{document_id}/attributes/SVNR/pseudonymize",
        json={"pseudonymized_by": "carol"},
        headers={"X-DMS-Principal": f"unpriv-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_pseudonymize_404_for_unknown_document(client):
    response = _pseudonymize(client, "does-not-exist")
    assert response.status_code == 404


def test_pseudonymize_rejects_attribute_not_marked_personal_data(client, document_id):
    response = _pseudonymize(client, document_id, attribute_name="Betreff")
    assert response.status_code == 400
    assert "personal_data" in response.json()["detail"]


def test_pseudonymize_rejects_unknown_attribute(client, document_id):
    response = _pseudonymize(client, document_id, attribute_name="does-not-exist")
    assert response.status_code == 400


def test_pseudonymize_rejects_attribute_with_no_value(client, object_type_id):
    document_id = client.post(
        "/documents",
        data={
            "title": "Ohne SVNR",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": '{"Betreff": "Testfall"}',
        },
        files={"file": ("x.pdf", b"%PDF-1.4 test", "application/pdf")},
    ).json()["id"]
    response = _pseudonymize(client, document_id)
    assert response.status_code == 400


def test_pseudonymize_succeeds_and_overwrites_the_live_value(client, document_id):
    response = _pseudonymize(client, document_id, reason="DSGVO-Löschanfrage")
    assert response.status_code == 201
    body = response.json()
    assert body["document_id"] == document_id
    assert body["attribute_name"] == "SVNR"
    assert body["pseudonymized_by"] == "carol"
    assert body["reason"] == "DSGVO-Löschanfrage"
    assert body["last_revealed_at"] is None
    assert "encrypted_value" not in body

    document = client.get(f"/documents/{document_id}").json()
    assert document["attributes"]["SVNR"] == "[PSEUDONYMISIERT]"
    # The unrelated, non-personal-data attribute is untouched.
    assert document["attributes"]["Betreff"] == "Testfall"


def test_pseudonymize_twice_returns_409(client, document_id):
    first = _pseudonymize(client, document_id)
    assert first.status_code == 201
    second = _pseudonymize(client, document_id)
    assert second.status_code == 409


def test_reveal_requires_reveal_permission_not_pseudonymization_permission(client, document_id):
    _pseudonymize(client, document_id)
    response = client.post(
        f"/documents/{document_id}/attributes/SVNR/reveal",
        json={"revealed_by": "dave"},
        headers=PSEUDONYMIZATION_ADMIN_HEADERS,
    )
    assert response.status_code == 403


def test_reveal_404_for_a_not_pseudonymized_attribute(client, document_id):
    response = client.post(
        f"/documents/{document_id}/attributes/SVNR/reveal",
        json={"revealed_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )
    assert response.status_code == 404


def test_reveal_returns_the_original_value_without_restoring_it_live(client, document_id):
    _pseudonymize(client, document_id, reason="DSGVO-Löschanfrage")

    response = client.post(
        f"/documents/{document_id}/attributes/SVNR/reveal",
        json={"revealed_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "123-45-6789"
    assert body["reason"] == "DSGVO-Löschanfrage"
    assert body["pseudonymized_by"] == "carol"

    # The live document attribute stays the placeholder - reveal is
    # transient, not a restore.
    document = client.get(f"/documents/{document_id}").json()
    assert document["attributes"]["SVNR"] == "[PSEUDONYMISIERT]"


def test_reveal_updates_last_revealed_tracking_on_the_listing(client, document_id):
    _pseudonymize(client, document_id)
    client.post(
        f"/documents/{document_id}/attributes/SVNR/reveal",
        json={"revealed_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )

    listing = client.get(f"/documents/{document_id}/attributes/pseudonymized").json()
    assert len(listing) == 1
    assert listing[0]["last_revealed_by"] == "dave"
    assert listing[0]["last_revealed_at"] is not None


def test_list_pseudonymized_attributes_empty_for_a_document_with_none(client, document_id):
    response = client.get(f"/documents/{document_id}/attributes/pseudonymized")
    assert response.status_code == 200
    assert response.json() == []


# --- Restore / "un-pseudonymize" (P62-S2, ADR 0156's own named follow-up) --


def test_restore_requires_reveal_permission_not_pseudonymization_permission(client, document_id):
    _pseudonymize(client, document_id)
    response = client.post(
        f"/documents/{document_id}/attributes/SVNR/restore",
        json={"restored_by": "dave"},
        headers=PSEUDONYMIZATION_ADMIN_HEADERS,
    )
    assert response.status_code == 403


def test_restore_404_for_a_not_pseudonymized_attribute(client, document_id):
    response = client.post(
        f"/documents/{document_id}/attributes/SVNR/restore",
        json={"restored_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )
    assert response.status_code == 404


def test_restore_writes_the_original_value_back_and_deletes_the_vault_entry(client, document_id):
    _pseudonymize(client, document_id, reason="DSGVO-Löschanfrage")

    response = client.post(
        f"/documents/{document_id}/attributes/SVNR/restore",
        json={"restored_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document_id
    assert body["attribute_name"] == "SVNR"
    assert body["value"] == "123-45-6789"
    assert body["restored_by"] == "dave"

    # The live document attribute is genuinely restored, unlike `reveal`.
    document = client.get(f"/documents/{document_id}").json()
    assert document["attributes"]["SVNR"] == "123-45-6789"
    assert document["attributes"]["Betreff"] == "Testfall"

    # The attribute is no longer pseudonymized - the vault entry is gone.
    listing = client.get(f"/documents/{document_id}/attributes/pseudonymized").json()
    assert listing == []


def test_restore_can_be_pseudonymized_again_after_restoring(client, document_id):
    """Confirms `restore` genuinely deletes the vault entry rather than
    just clearing the live value - a second pseudonymize/restore cycle
    must work exactly like the first, not hit `AlreadyPseudonymizedError`."""
    _pseudonymize(client, document_id)
    client.post(
        f"/documents/{document_id}/attributes/SVNR/restore",
        json={"restored_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )

    second = _pseudonymize(client, document_id)
    assert second.status_code == 201


# --- Automatic retention-expiry trigger (5.2, Phase 58 Session 1) ---------


def test_put_retention_rejects_both_full_deletion_and_retention_pseudonymize(client, document_id):
    response = client.put(
        f"/documents/{document_id}/retention",
        json={
            "retention_until": "2030-01-01T00:00:00Z",
            "full_deletion": True,
            "retention_pseudonymize": True,
        },
        headers=PSEUDONYMIZATION_ADMIN_HEADERS
        | {"X-DMS-Principal": "document-service-test-retention-admin"},
    )
    assert response.status_code == 422


def test_put_retention_sets_retention_pseudonymize_field(client, document_id):
    response = client.put(
        f"/documents/{document_id}/retention",
        json={"retention_until": "2030-01-01T00:00:00Z", "retention_pseudonymize": True},
        headers={"X-DMS-Principal": "document-service-test-retention-admin"},
    )
    assert response.status_code == 200
    assert response.json()["retention_pseudonymize"] is True
    assert response.json()["full_deletion"] is False


async def test_pseudonymize_eligible_attributes_pseudonymizes_only_personal_data_with_a_value(
    client, session, document_id, poll_object_type_client
):
    """Direct unit test of the retention-poll-loop helper (same style as
    `test_retention_actions.py`'s direct calls into poll-loop-adjacent
    functions) - `SVNR` is `personal_data: true` and has a value, `Betreff`
    is not marked `personal_data` and must stay untouched."""
    from document_service import main, repository

    document = await repository.get_document(session, document_id)
    pseudonymized = await main._pseudonymize_eligible_attributes(
        session,
        document,
        poll_object_type_client,
        triggered_by="system:retention-poll",
        reason="Test",
    )
    await session.commit()

    assert pseudonymized == ["SVNR"]
    updated = await repository.get_document(session, document_id)
    assert updated.attributes["SVNR"] == "[PSEUDONYMISIERT]"
    assert updated.attributes["Betreff"] == "Testfall"
    vault_entries = await repository.list_pseudonymized_attributes(session, document_id)
    assert len(vault_entries) == 1
    assert vault_entries[0].pseudonymized_by == "system:retention-poll"


async def test_pseudonymize_eligible_attributes_skips_an_already_pseudonymized_attribute(
    client, session, document_id, poll_object_type_client
):
    from document_service import main, repository

    document = await repository.get_document(session, document_id)
    first = await main._pseudonymize_eligible_attributes(
        session,
        document,
        poll_object_type_client,
        triggered_by="system:retention-poll",
        reason="Test",
    )
    await session.commit()
    assert first == ["SVNR"]

    document = await repository.get_document(session, document_id)
    second = await main._pseudonymize_eligible_attributes(
        session,
        document,
        poll_object_type_client,
        triggered_by="system:retention-poll",
        reason="Test",
    )
    assert second == []


async def test_pseudonymize_eligible_attributes_returns_empty_for_a_document_without_object_type(
    client, session, poll_object_type_client
):
    from document_service import main, repository

    document_id = client.post(
        "/documents",
        data={"title": "Ohne Objekttyp", "created_by": "alice"},
        files={"file": ("x.pdf", b"%PDF-1.4 test", "application/pdf")},
    ).json()["id"]

    document = await repository.get_document(session, document_id)
    pseudonymized = await main._pseudonymize_eligible_attributes(
        session,
        document,
        poll_object_type_client,
        triggered_by="system:retention-poll",
        reason="Test",
    )
    assert pseudonymized == []
