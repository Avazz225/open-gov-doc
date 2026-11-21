"""virus-scan-service sensor (P88-S2b, package B). Scanning is synchronous,
so there is no backlog; what waits for someone is the quarantine - files
found infected and neither released nor purged yet."""

from __future__ import annotations

from dms_metrics_client import SensorRegistry, SensorSpec
from sqlalchemy import func, select

from virus_scan_service.models import ScanResult

QUARANTINED = SensorSpec(
    name="virus_scan.quarantined",
    group="security",
    cost="cheap",
    description="Als infiziert erkannte Dateien in Quarantaene (weder freigegeben noch geloescht)",
)


def build_sensors(registry: SensorRegistry, app) -> None:
    async def quarantined() -> float:
        async with app.state.session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(ScanResult).where(ScanResult.status == "infected")
            )
        return float(count or 0)

    registry.on_scrape_async(registry.gauge(QUARANTINED), quarantined)
