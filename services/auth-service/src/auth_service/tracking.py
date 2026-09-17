from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service.models import UserTrackingConfig, UserTrackingRetentionConfig, UserTrackingSession

_RETENTION_CONFIG_ID = 1


async def is_tracking_enabled(session: AsyncSession, principal_id: str) -> bool:
    """Pure per-principal lookup (5.5, Post-Roadmap Phase 41 Session 3,
    ADR 0157) - deliberately unaware of the superuser's own activation-
    tied default (see `main.py._maybe_track_session_event`, which layers
    that on top): a missing row means "not tracked", the concept's own
    default-off stance for every account other than the activated
    superuser."""
    config = await session.get(UserTrackingConfig, principal_id)
    return config is not None and config.enabled


async def set_tracking_enabled(
    session: AsyncSession, principal_id: str, *, enabled: bool, updated_by: str
) -> UserTrackingConfig:
    config = await session.get(UserTrackingConfig, principal_id)
    if config is None:
        config = UserTrackingConfig(principal_id=principal_id, enabled=enabled)
        session.add(config)
    else:
        config.enabled = enabled
    config.updated_by = updated_by
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def record_session_event(
    session: AsyncSession,
    *,
    principal_id: str,
    username: str,
    event_type: str,
    auth_method: str,
    client_ip: str | None,
    user_agent: str | None,
) -> UserTrackingSession:
    """One row per tracked login/refresh (5.5) - `client_ip`/`user_agent`
    are exactly what's actually determinable server-side from the
    request (`X-DMS-Client-IP`, forwarded by the gateway since this
    session, ADR 0157; the standard `User-Agent` header, passed through
    unchanged by `gateway_service.upstream.filter_headers`) - no GeoIP
    lookup, no client-side canvas/font fingerprinting (deliberately out
    of scope, see ADR 0157 "Rationale")."""
    event = UserTrackingSession(
        principal_id=principal_id,
        username=username,
        event_type=event_type,
        auth_method=auth_method,
        client_ip=client_ip,
        user_agent=user_agent[:512] if user_agent else None,
        occurred_at=datetime.now(UTC),
    )
    session.add(event)
    await session.flush()
    return event


async def list_tracked_sessions(
    session: AsyncSession, *, principal_id: str | None = None, limit: int = 200
) -> list[UserTrackingSession]:
    query = select(UserTrackingSession)
    if principal_id is not None:
        query = query.where(UserTrackingSession.principal_id == principal_id)
    query = query.order_by(UserTrackingSession.occurred_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_or_create_retention_config(session: AsyncSession) -> UserTrackingRetentionConfig:
    """Get-or-create (5.5), same pattern as `main.py._get_or_create_sso_
    config` - concept default 7 days (deliberately shorter than the
    regular audit log, 5.3), configurable per installation."""
    config = await session.get(UserTrackingRetentionConfig, _RETENTION_CONFIG_ID)
    if config is None:
        config = UserTrackingRetentionConfig(
            id=_RETENTION_CONFIG_ID, retention_days=7, updated_at=datetime.now(UTC)
        )
        session.add(config)
        await session.flush()
    return config


async def update_retention_config(
    session: AsyncSession, *, retention_days: int
) -> UserTrackingRetentionConfig:
    config = await get_or_create_retention_config(session)
    config.retention_days = retention_days
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def purge_expired_sessions(session: AsyncSession, *, retention_days: int) -> int:
    """Called periodically by `main.py`'s `_tracking_retention_poll_loop`
    (same poll-instead-of-push idiom as `_superuser_poll_loop`) - auto-
    deletes tracked session rows older than the configured retention
    period, independent of the underlying regular audit event (which
    keeps its own, normal retention rules, 5.2) - the concept's own
    explicit requirement for a separate, shorter retention window."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = await session.execute(
        delete(UserTrackingSession).where(UserTrackingSession.occurred_at < cutoff)
    )
    await session.flush()
    return result.rowcount or 0
