"""archival-service sensors (P88-S2b, package B): transfers in progress and
transfers that gave up, for documents and for cases. A transient failure
keeps its phase and retries with backoff, so `failed_permanent` is the
only failed state."""

from __future__ import annotations

from dms_metrics_client import SensorRegistry, SensorSpec
from sqlalchemy import func, select

from archival_service.models import ArchivalTransfer, CaseArchivalTransfer
from archival_service.repository import _ACTIVE_CASE_STATUSES, _ACTIVE_STATUSES

ACTIVE = SensorSpec(
    name="archival.transfers.active",
    group="capacity",
    cost=3,
    description="Laufende Aussonderungs-Transfers je Art (document, case)",
)
FAILED = SensorSpec(
    name="archival.transfers.failed",
    group="reliability",
    cost=2,
    description="Endgueltig fehlgeschlagene Aussonderungs-Transfers je Art (document, case)",
)


async def _count(session, model, condition) -> float:
    return float(
        await session.scalar(select(func.count()).select_from(model).where(condition)) or 0
    )


def build_sensors(registry: SensorRegistry, app) -> None:
    async def active() -> dict[tuple[str, ...], float]:
        async with app.state.session_factory() as session:
            return {
                ("document",): await _count(
                    session, ArchivalTransfer, ArchivalTransfer.status.in_(_ACTIVE_STATUSES)
                ),
                ("case",): await _count(
                    session,
                    CaseArchivalTransfer,
                    CaseArchivalTransfer.status.in_(_ACTIVE_CASE_STATUSES),
                ),
            }

    async def failed() -> dict[tuple[str, ...], float]:
        async with app.state.session_factory() as session:
            return {
                ("document",): await _count(
                    session, ArchivalTransfer, ArchivalTransfer.status == "failed_permanent"
                ),
                ("case",): await _count(
                    session, CaseArchivalTransfer, CaseArchivalTransfer.status == "failed_permanent"
                ),
            }

    registry.on_scrape_async(registry.gauge(ACTIVE, labelnames=("kind",)), active)
    registry.on_scrape_async(registry.gauge(FAILED, labelnames=("kind",)), failed)
