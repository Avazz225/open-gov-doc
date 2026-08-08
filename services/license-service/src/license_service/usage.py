"""Nutzungspruefung (9.2: "prueft laufend ... aktuelle Nutzung gegen
Limits") - vereint die vier Konzept-9.1-Dimensionen (Nutzeranzahl,
Applikationskomponenten, Speichervolumen, Dokumentenzahl) zu einem
Snapshot. Jede Dimension ist `None`-limitierbar ("unlimited", Konzept 9.1
woertlich) - `None` bedeutet dann nie ueberschritten."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class DimensionUsage:
    limit: float | int | None
    current: float | int | None
    exceeded: bool


@dataclass
class UsageSnapshot:
    valid: bool
    invalid_reason: str | None
    issued_at: datetime | None
    expires_at: datetime | None
    days_remaining: int | None
    user_model: str | None
    users: DimensionUsage
    storage_gb: DimensionUsage
    documents: DimensionUsage
    licensed_components: list[str] | None
    limits_exceeded: list[str]


def _exceeded(limit: float | int | None, current: float | int | None) -> bool:
    if limit is None or current is None:
        return False
    return current > limit


def _to_datetime(epoch: float | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, tz=UTC) if epoch is not None else None


async def compute_usage(
    claims: dict,
    *,
    storage_client,
    document_client,
    auth_client,
) -> UsageSnapshot:
    now = datetime.now(UTC)
    issued_at = _to_datetime(claims.get("iat"))
    expires_at = _to_datetime(claims.get("exp"))
    nbf = _to_datetime(claims.get("nbf"))

    invalid_reason: str | None = None
    if expires_at is not None and now > expires_at:
        invalid_reason = "Lizenz abgelaufen"
    elif nbf is not None and now < nbf:
        invalid_reason = "Lizenz noch nicht gueltig"

    days_remaining = (expires_at - now).days if expires_at is not None else None

    user_model = claims.get("user_model")
    max_users = claims.get("max_users")
    current_users = (
        await auth_client.named_user_count()
        if user_model == "named"
        else await auth_client.concurrent_session_count()
    )
    users = DimensionUsage(
        limit=max_users, current=current_users, exceeded=_exceeded(max_users, current_users)
    )

    storage_limit_gb = claims.get("storage_limit_gb")
    current_storage_gb = (await storage_client.total_bytes()) / (1024**3)
    storage = DimensionUsage(
        limit=storage_limit_gb,
        current=current_storage_gb,
        exceeded=_exceeded(storage_limit_gb, current_storage_gb),
    )

    document_limit = claims.get("document_limit")
    current_documents = await document_client.count_active_total()
    documents = DimensionUsage(
        limit=document_limit,
        current=current_documents,
        exceeded=_exceeded(document_limit, current_documents),
    )

    limits_exceeded = [
        name
        for name, dim in (("users", users), ("storage_gb", storage), ("documents", documents))
        if dim.exceeded
    ]

    return UsageSnapshot(
        valid=invalid_reason is None,
        invalid_reason=invalid_reason,
        issued_at=issued_at,
        expires_at=expires_at,
        days_remaining=days_remaining,
        user_model=user_model,
        users=users,
        storage_gb=storage,
        documents=documents,
        licensed_components=claims.get("licensed_components"),
        limits_exceeded=limits_exceeded,
    )
