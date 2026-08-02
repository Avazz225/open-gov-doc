import uuid

import pytest
from federation_hub_service import repository
from federation_hub_service.schemas import InstallationRegister


def make_payload(**overrides) -> InstallationRegister:
    data = {
        "id": f"install-{uuid.uuid4().hex[:8]}",
        "display_name": "Test-Installation",
        "callback_base_url": "http://example.test",
        "public_key_pem": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----\n",
        "version": "1.0",
        "min_compatible_peer_version": "1.0",
    }
    data.update(overrides)
    return InstallationRegister(**data)


async def test_register_new_installation_returns_api_key(session):
    payload = make_payload()

    installation, api_key = await repository.register_or_update_installation(
        session, payload, presented_api_key=None
    )

    assert installation.id == payload.id
    assert api_key is not None and len(api_key) > 20


async def test_register_existing_installation_without_key_rejected(session):
    payload = make_payload()
    await repository.register_or_update_installation(session, payload, presented_api_key=None)

    with pytest.raises(repository.UnauthorizedError):
        await repository.register_or_update_installation(session, payload, presented_api_key=None)


async def test_register_existing_installation_with_correct_key_updates(session):
    payload = make_payload()
    _, api_key = await repository.register_or_update_installation(
        session, payload, presented_api_key=None
    )

    updated_payload = make_payload(id=payload.id, display_name="Neuer Name")
    installation, returned_key = await repository.register_or_update_installation(
        session, updated_payload, presented_api_key=api_key
    )

    assert installation.display_name == "Neuer Name"
    assert returned_key is None


async def test_deregister_requires_matching_key(session):
    payload = make_payload()
    _, api_key = await repository.register_or_update_installation(
        session, payload, presented_api_key=None
    )

    with pytest.raises(repository.UnauthorizedError):
        await repository.deregister_installation(session, payload.id, presented_api_key="wrong")

    await repository.deregister_installation(session, payload.id, presented_api_key=api_key)

    installations = await repository.list_installations(session)
    assert payload.id not in [i.id for i in installations]


async def test_get_installation_by_api_key(session):
    payload = make_payload()
    _, api_key = await repository.register_or_update_installation(
        session, payload, presented_api_key=None
    )

    found = await repository.get_installation_by_api_key(session, api_key)
    assert found is not None
    assert found.id == payload.id

    assert await repository.get_installation_by_api_key(session, "does-not-exist") is None


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
    payload_a = make_payload(version=a_version, min_compatible_peer_version=a_min_peer)
    payload_b = make_payload(version=b_version, min_compatible_peer_version=b_min_peer)
    installation_a, _ = await repository.register_or_update_installation(
        session, payload_a, presented_api_key=None
    )
    installation_b, _ = await repository.register_or_update_installation(
        session, payload_b, presented_api_key=None
    )

    assert repository.is_version_compatible(installation_a, installation_b) is expected


async def test_hub_identity_singleton(session):
    first = await repository.get_or_create_hub_identity(session)
    second = await repository.get_or_create_hub_identity(session)

    assert first.private_key_pem == second.private_key_pem
    assert first.public_key_pem == second.public_key_pem
