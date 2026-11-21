import asyncio
import shutil

from dms_metrics_client import SensorRegistry, SensorSpec
from sqlalchemy import func, select

from storage_service import repository
from storage_service.identity_guard import IDENTITY_KEY
from storage_service.models import ObjectCopy

# Sensor concept (10.1, Phase 40 Session 4) - this service's first custom
# sensor (previously only the generic HTTP ones). Deliberately updated by
# `GET /metrics` itself (querying `repository.count_pending_copies_by_
# backend` fresh on every scrape) rather than a periodic background loop
# like every other sensor-using service's samplers - storage-service
# deliberately has ZERO in-process background tasks (ADR 0004: "explicit
# endpoint instead of implicit background task"), and a new
# `asyncio.create_task` sampler loop would have been the first one ever,
# cutting against that ADR's own reasoning even though read-only. Pull-
# on-scrape instead matches ADR 0004's philosophy exactly: `/metrics` is
# itself an explicit, externally-triggered call (the monitoring scraper
# hitting it on its own schedule), the same idiom as the external
# CronJob hitting `POST /replication/process-pending` on ITS own
# schedule (see docs/services/storage-service.md).
REPLICATION_BACKLOG = SensorSpec(
    name="storage.replication.backlog",
    group="reliability",
    cost="cheap",
    description=(
        "Anzahl object_copy-Zeilen mit Status 'pending'/'failed' ueber alle "
        "konfigurierten Ziele hinweg (Replikations-Rueckstand, 3.6)"
    ),
)


# P88-S2b (packages A and B): target health and fill level, and copies
# that gave up for good. Also pull-on-scrape, for the same ADR 0004 reason.
TARGET_HEALTHY = SensorSpec(
    name="storage.target.healthy",
    group="reliability",
    cost="cheap",
    description="Erreichbarkeit je Speicherziel (1 erreichbar, 0 nicht) - ein Lesezugriff je Abruf",
)
TARGET_USED_PERCENT = SensorSpec(
    name="storage.target.used_percent",
    group="capacity",
    cost="cheap",
    description="Belegung des Dateisystems je lokalem Speicherziel in Prozent",
)
REPLICATION_FAILED_PERMANENT = SensorSpec(
    name="storage.replication.failed_permanent",
    group="reliability",
    cost="cheap",
    description="Kopien, deren Replikation endgueltig aufgegeben wurde (failed_permanent)",
)

PROBE_TIMEOUT_SECONDS = 3.0


def build_sensors(registry: SensorRegistry, app) -> None:
    """`app` is read at scrape time: targets, backends and the session
    factory only exist once the lifespan has run."""

    def active_targets():
        return [t for t in app.state.target_configs if not t.decommissioned]

    async def backlog() -> float:
        async with app.state.session_factory() as session:
            counts = await repository.count_pending_copies_by_backend(session)
        return float(sum(counts.values()))

    async def failed_permanent() -> float:
        async with app.state.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(ObjectCopy)
                .where(ObjectCopy.status == "failed_permanent")
            )
        return float(count or 0)

    async def healthy() -> dict[tuple[str, ...], float]:
        async def probe(target_id: str) -> float:
            backend = app.state.backends.get(target_id)
            if backend is None:
                return 0.0
            try:
                async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                    await backend.exists(IDENTITY_KEY)
            except Exception:
                return 0.0
            return 1.0

        targets = active_targets()
        results = await asyncio.gather(*(probe(t.id) for t in targets))
        return {(t.id,): value for t, value in zip(targets, results, strict=True)}

    def used_percent() -> dict[tuple[str, ...], float]:
        values = {}
        for target in active_targets():
            if target.type != "local" or not target.base_path:
                continue
            try:
                usage = shutil.disk_usage(target.base_path)
            except OSError:
                continue
            values[(target.id,)] = usage.used / usage.total * 100 if usage.total else 0.0
        return values

    registry.on_scrape_async(registry.gauge(REPLICATION_BACKLOG), backlog)
    registry.on_scrape_async(registry.gauge(REPLICATION_FAILED_PERMANENT), failed_permanent)
    registry.on_scrape_async(registry.gauge(TARGET_HEALTHY, labelnames=("target",)), healthy)
    registry.on_scrape(registry.gauge(TARGET_USED_PERCENT, labelnames=("target",)), used_percent)
