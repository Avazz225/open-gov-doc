from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from license_service.usage import compute_usage


def _claims(**overrides) -> dict:
    now = datetime.now(UTC)
    base = {
        "iat": (now - timedelta(days=1)).timestamp(),
        "exp": (now + timedelta(days=30)).timestamp(),
        "user_model": "concurrent",
        "max_users": 10,
        "storage_limit_gb": 100.0,
        "document_limit": 1000,
        "licensed_components": None,
    }
    base.update(overrides)
    return base


def _clients(*, sessions=3, users=5, storage_bytes=1024**3, documents=50):
    storage_client = AsyncMock()
    storage_client.total_bytes.return_value = storage_bytes
    document_client = AsyncMock()
    document_client.count_active_total.return_value = documents
    auth_client = AsyncMock()
    auth_client.concurrent_session_count.return_value = sessions
    auth_client.named_user_count.return_value = users
    return storage_client, document_client, auth_client


async def test_valid_license_within_all_limits():
    storage_client, document_client, auth_client = _clients()

    snapshot = await compute_usage(
        _claims(),
        storage_client=storage_client,
        document_client=document_client,
        auth_client=auth_client,
    )

    assert snapshot.valid
    assert snapshot.invalid_reason is None
    assert snapshot.limits_exceeded == []
    assert snapshot.users.current == 3
    assert snapshot.storage_gb.current == 1.0
    assert snapshot.documents.current == 50


async def test_expired_license_is_invalid():
    storage_client, document_client, auth_client = _clients()
    claims = _claims(
        iat=(datetime.now(UTC) - timedelta(days=400)).timestamp(),
        exp=(datetime.now(UTC) - timedelta(days=10)).timestamp(),
    )

    snapshot = await compute_usage(
        claims,
        storage_client=storage_client,
        document_client=document_client,
        auth_client=auth_client,
    )

    assert not snapshot.valid
    assert snapshot.invalid_reason == "Lizenz abgelaufen"
    assert snapshot.days_remaining < 0


async def test_not_yet_valid_license_is_invalid():
    storage_client, document_client, auth_client = _clients()
    claims = _claims(nbf=(datetime.now(UTC) + timedelta(days=5)).timestamp())

    snapshot = await compute_usage(
        claims,
        storage_client=storage_client,
        document_client=document_client,
        auth_client=auth_client,
    )

    assert not snapshot.valid
    assert snapshot.invalid_reason == "Lizenz noch nicht gueltig"


async def test_named_user_model_uses_user_count_not_session_count():
    storage_client, document_client, auth_client = _clients(sessions=999, users=7)
    claims = _claims(user_model="named", max_users=10)

    snapshot = await compute_usage(
        claims,
        storage_client=storage_client,
        document_client=document_client,
        auth_client=auth_client,
    )

    assert snapshot.users.current == 7
    auth_client.named_user_count.assert_awaited_once()
    auth_client.concurrent_session_count.assert_not_called()


async def test_unlimited_dimension_never_exceeded():
    storage_client, document_client, auth_client = _clients(documents=10_000_000)
    claims = _claims(document_limit=None)

    snapshot = await compute_usage(
        claims,
        storage_client=storage_client,
        document_client=document_client,
        auth_client=auth_client,
    )

    assert snapshot.documents.exceeded is False
    assert "documents" not in snapshot.limits_exceeded


async def test_exceeded_dimensions_are_listed():
    storage_client, document_client, auth_client = _clients(sessions=99, documents=5000)
    claims = _claims(max_users=10, document_limit=1000)

    snapshot = await compute_usage(
        claims,
        storage_client=storage_client,
        document_client=document_client,
        auth_client=auth_client,
    )

    assert set(snapshot.limits_exceeded) == {"users", "documents"}
    assert snapshot.users.exceeded
    assert snapshot.documents.exceeded
    assert not snapshot.storage_gb.exceeded
