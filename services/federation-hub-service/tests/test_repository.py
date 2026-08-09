import json
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from federation_hub_service import repository
from federation_hub_service.crypto_utils import sign_body
from federation_hub_service.schemas import InstallationRegister


def _generate_keypair() -> tuple[bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


def make_payload(*, public_key_pem: str, **overrides) -> InstallationRegister:
    data = {
        "id": f"install-{uuid.uuid4().hex[:8]}",
        "display_name": "Test-Installation",
        "callback_base_url": "http://example.test",
        "public_key_pem": public_key_pem,
        "version": "1.0",
        "min_compatible_peer_version": "1.0",
    }
    data.update(overrides)
    return InstallationRegister(**data)


def _raw_body(payload: InstallationRegister) -> bytes:
    return json.dumps(payload.model_dump()).encode("utf-8")


async def register(session, private_key_pem: bytes, payload: InstallationRegister):
    body = _raw_body(payload)
    return await repository.register_or_update_installation(
        session, payload, raw_body=body, presented_signature=sign_body(private_key_pem, body)
    )


async def test_register_new_installation_requires_matching_signature(session):
    private_pem, public_pem = _generate_keypair()
    other_private_pem, _ = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    body = _raw_body(payload)

    with pytest.raises(repository.UnauthorizedError):
        await repository.register_or_update_installation(
            session, payload, raw_body=body, presented_signature=sign_body(other_private_pem, body)
        )

    installation = await register(session, private_pem, payload)
    assert installation.id == payload.id


async def test_register_existing_installation_without_signature_rejected(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    body = _raw_body(payload)
    with pytest.raises(repository.UnauthorizedError):
        await repository.register_or_update_installation(
            session, payload, raw_body=body, presented_signature=None
        )


async def test_register_existing_installation_with_correct_signature_updates(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    updated_payload = make_payload(
        id=payload.id, public_key_pem=public_pem, display_name="Neuer Name"
    )
    installation = await register(session, private_pem, updated_payload)
    assert installation.display_name == "Neuer Name"


async def test_register_update_ignores_submitted_public_key_change(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    _, other_public_pem = _generate_keypair()
    updated_payload = make_payload(id=payload.id, public_key_pem=other_public_pem)
    installation = await register(session, private_pem, updated_payload)
    assert installation.public_key_pem == public_pem


async def test_register_rejects_revoked_installation(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)
    await repository.revoke_installation(session, payload.id, reason="test")

    body = _raw_body(payload)
    with pytest.raises(repository.UnauthorizedError):
        await repository.register_or_update_installation(
            session, payload, raw_body=body, presented_signature=sign_body(private_pem, body)
        )


async def test_deregister_requires_matching_signature(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    with pytest.raises(repository.UnauthorizedError):
        await repository.deregister_installation(
            session, payload.id, presented_signature=sign_body(private_pem, b"wrong-bytes")
        )

    await repository.deregister_installation(
        session, payload.id, presented_signature=sign_body(private_pem, payload.id.encode("utf-8"))
    )

    installations = await repository.list_installations(session)
    assert payload.id not in [i.id for i in installations]


async def test_rotate_installation_key_requires_current_signature(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    new_private_pem, new_public_pem = _generate_keypair()
    rotate_body = json.dumps({"new_public_key_pem": new_public_pem}).encode("utf-8")

    with pytest.raises(repository.UnauthorizedError):
        await repository.rotate_installation_key(
            session,
            payload.id,
            raw_body=rotate_body,
            new_public_key_pem=new_public_pem,
            presented_signature=sign_body(new_private_pem, rotate_body),
        )

    installation = await repository.rotate_installation_key(
        session,
        payload.id,
        raw_body=rotate_body,
        new_public_key_pem=new_public_pem,
        presented_signature=sign_body(private_pem, rotate_body),
    )
    assert installation.public_key_pem == new_public_pem


async def test_revoke_installation_sets_timestamp_and_reason(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    installation = await repository.revoke_installation(
        session, payload.id, reason="Schlüssel kompromittiert"
    )
    assert installation.revoked_at is not None
    assert installation.revoked_reason == "Schlüssel kompromittiert"


async def test_authenticate_signed_request(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    body = b"some-request-body"
    installation = await repository.authenticate_signed_request(
        session, installation_id=payload.id, body=body, signature=sign_body(private_pem, body)
    )
    assert installation.id == payload.id

    with pytest.raises(repository.UnauthorizedError):
        await repository.authenticate_signed_request(
            session, installation_id=payload.id, body=body, signature="not-a-valid-signature"
        )

    with pytest.raises(repository.UnauthorizedError):
        await repository.authenticate_signed_request(
            session, installation_id="does-not-exist", body=body, signature="anything"
        )


@pytest.mark.parametrize(
    ("a_version", "a_min_peer", "b_version", "b_min_peer", "expected"),
    [
        ("1.0", "1.0", "1.0", "1.0", True),
        ("2.3", "1.0", "1.5", "1.0", True),
        ("1.0", "2.0", "1.5", "1.0", False),
        ("2.0", "1.0", "1.5", "3.0", False),
    ],
)
async def test_is_version_compatible(
    session, a_version, a_min_peer, b_version, b_min_peer, expected
):
    private_a, public_a = _generate_keypair()
    private_b, public_b = _generate_keypair()
    payload_a = make_payload(
        public_key_pem=public_a, version=a_version, min_compatible_peer_version=a_min_peer
    )
    payload_b = make_payload(
        public_key_pem=public_b, version=b_version, min_compatible_peer_version=b_min_peer
    )
    installation_a = await register(session, private_a, payload_a)
    installation_b = await register(session, private_b, payload_b)

    assert repository.is_version_compatible(installation_a, installation_b) is expected


async def test_hub_identity_singleton(session):
    first = await repository.get_or_create_hub_identity(session)
    second = await repository.get_or_create_hub_identity(session)

    assert first.private_key_pem == second.private_key_pem
    assert first.public_key_pem == second.public_key_pem
