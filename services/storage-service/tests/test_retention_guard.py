from datetime import UTC, datetime, timedelta

from storage_service import repository, retention_guard
from storage_service.settings import BackendTargetConfig, Settings

FUTURE = datetime.now(UTC) + timedelta(days=1)
PAST = datetime.now(UTC) - timedelta(days=1)

GOVERNANCE_TARGET = BackendTargetConfig(
    id="s3-governance",
    type="s3",
    endpoint_url="http://x",
    access_key="a",
    secret_key="b",
    bucket="b",
    region="us-east-1",
    object_lock_mode="governance",
)
PLAIN_TARGET = BackendTargetConfig(id="local", type="local", base_path="/tmp/x")


def test_has_governance_bypass_role_matches_configured_role():
    settings = Settings(governance_bypass_role="dms-admin")
    assert retention_guard.has_governance_bypass_role("dms-user,dms-admin", settings) is True
    assert retention_guard.has_governance_bypass_role("dms-user", settings) is False
    assert retention_guard.has_governance_bypass_role("", settings) is False


async def test_find_locked_targets_blocks_future_retention_on_governance_target(session):
    await repository.upsert_metadata(
        session,
        object_key="k1",
        backend="s3-governance",
        checksum_sha256="x",
        size_bytes=1,
        content_type=None,
    )
    await repository.record_copy(
        session, "k1", "s3-governance", status="ok", retention_until=FUTURE
    )

    blocked = await retention_guard.find_locked_targets(session, "k1", targets=[GOVERNANCE_TARGET])

    assert blocked == ["s3-governance"]


async def test_find_locked_targets_ignores_expired_retention(session):
    await repository.upsert_metadata(
        session,
        object_key="k2",
        backend="s3-governance",
        checksum_sha256="x",
        size_bytes=1,
        content_type=None,
    )
    await repository.record_copy(session, "k2", "s3-governance", status="ok", retention_until=PAST)

    blocked = await retention_guard.find_locked_targets(session, "k2", targets=[GOVERNANCE_TARGET])

    assert blocked == []


async def test_find_locked_targets_ignores_targets_without_governance_mode(session):
    """Ein Ziel mit gesetztem retention_until, aber OHNE object_lock_mode,
    blockiert bewusst nicht - siehe retention_guard.py Docstring."""
    await repository.upsert_metadata(
        session,
        object_key="k3",
        backend="local",
        checksum_sha256="x",
        size_bytes=1,
        content_type=None,
    )
    await repository.record_copy(session, "k3", "local", status="ok", retention_until=FUTURE)

    blocked = await retention_guard.find_locked_targets(session, "k3", targets=[PLAIN_TARGET])

    assert blocked == []


async def test_find_locked_targets_returns_empty_without_any_retention(session):
    await repository.upsert_metadata(
        session,
        object_key="k4",
        backend="s3-governance",
        checksum_sha256="x",
        size_bytes=1,
        content_type=None,
    )
    await repository.record_copy(session, "k4", "s3-governance", status="ok")

    blocked = await retention_guard.find_locked_targets(session, "k4", targets=[GOVERNANCE_TARGET])

    assert blocked == []
