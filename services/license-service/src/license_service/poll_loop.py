"""Laufende Nutzungspruefung (9.2: "prueft laufend, nicht nur beim Start") -
gleiches Poll-Loop-Idiom wie document-service's `_retention_poll_loop`/
workflow-service's SLA-Timer (ADR 0020): ein Fehler in einem Tick bricht die
Schleife nicht ab. Publiziert die drei in 9.2 genannten Statusaenderungs-
Arten nur bei einem tatsaechlichen Zustandswechsel (Flankenerkennung gegen
`InstalledLicense.last_status_snapshot`), nicht bei jedem Tick."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from license_service import license_state, usage
from license_service.clients import AuthServiceClient, DocumentClient, StorageClient
from license_service.license_verifier import InvalidLicenseError, decode
from license_service.models import InstalledLicense
from license_service.settings import Settings
from license_service.usage import UsageSnapshot

logger = logging.getLogger(__name__)

PublishEvent = Callable[[str, str | None, dict, str | None], Awaitable[None]]


@dataclass
class UsageClients:
    storage_client: StorageClient
    document_client: DocumentClient
    auth_client: AuthServiceClient


_ACTOR = "system:license-poll"


async def _diff_and_publish(
    license_row: InstalledLicense,
    snapshot: UsageSnapshot,
    *,
    expiring_soon_threshold_days: int,
    publish_event: PublishEvent,
) -> None:
    previous = license_row.last_status_snapshot or {}
    is_expiring_soon = (
        snapshot.days_remaining is not None
        and 0 <= snapshot.days_remaining <= expiring_soon_threshold_days
    )

    if not snapshot.valid and not previous.get("invalid", False):
        await publish_event("license.invalid", None, {"reason": snapshot.invalid_reason}, _ACTOR)
    if is_expiring_soon and not previous.get("expiring_soon", False):
        await publish_event(
            "license.expiring_soon", None, {"days_remaining": snapshot.days_remaining}, _ACTOR
        )

    previously_exceeded = set(previous.get("limits_exceeded", []))
    dimensions = {
        "users": snapshot.users,
        "storage_gb": snapshot.storage_gb,
        "documents": snapshot.documents,
    }
    for dimension_name in snapshot.limits_exceeded:
        if dimension_name in previously_exceeded:
            continue
        dimension = dimensions[dimension_name]
        await publish_event(
            "license.limit_exceeded",
            None,
            {"dimension": dimension_name, "current": dimension.current, "limit": dimension.limit},
            _ACTOR,
        )

    license_row.last_status_snapshot = {
        "invalid": not snapshot.valid,
        "expiring_soon": is_expiring_soon,
        "limits_exceeded": sorted(snapshot.limits_exceeded),
    }


async def run_tick(
    session_factory: async_sessionmaker[AsyncSession],
    clients: UsageClients,
    settings: Settings,
    publish_event: PublishEvent,
) -> None:
    async with session_factory() as session:
        license_row = await license_state.get_installed(session)
        if license_row is None:
            return
        try:
            claims = decode(license_row.raw_token, public_key_pem=settings.license_public_key_pem)
        except InvalidLicenseError:
            logger.warning("license_poll_signature_invalid")
            return
        snapshot = await usage.compute_usage(
            claims,
            storage_client=clients.storage_client,
            document_client=clients.document_client,
            auth_client=clients.auth_client,
        )
        await _diff_and_publish(
            license_row,
            snapshot,
            expiring_soon_threshold_days=settings.license_expiring_soon_threshold_days,
            publish_event=publish_event,
        )
        await session.commit()


async def poll_loop(
    session_factory: async_sessionmaker[AsyncSession],
    clients: UsageClients,
    settings: Settings,
    publish_event: PublishEvent,
) -> None:
    while True:
        try:
            await run_tick(session_factory, clients, settings, publish_event)
        except Exception:
            logger.exception("license_poll_tick_failed")
        await asyncio.sleep(settings.license_poll_interval_seconds)
