from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from dms_metrics_client import (
    GuardedGauge,
    GuardedHistogram,
    SensorRegistry,
    SensorSpec,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from document_service import repository
from document_service.models import Document, DocumentLock

# document-service was one of the two original sensor pilots (10.1,
# P11-S1, see P11-S0 finding) - both bespoke sensor names below are
# deliberately taken verbatim from concept 10.1's own example list. Now
# additionally covered by the generic http.* sensors from the full rollout
# (see `main.py`'s module-level `bootstrap_http_sensors` call, which builds
# the `SensorRegistry` this module's sensors are added to).
UPLOAD_DURATION = SensorSpec(
    name="document.upload.duration",
    group="performance",
    cost=4,
    description="Dauer eines Dokument-Uploads (Virenscan + Storage-Schreibvorgang)",
)
ACTIVE_DOCUMENTS = SensorSpec(
    name="document.count.active_total",
    group="capacity",
    cost=2,
    description="Anzahl installationsweit aktiver Dokumente",
)


# P88-S2b (package B): the oldest open checkout lock, and trash entries past
# their restore period that are still waiting for the purge - the closest
# thing to a "purge backlog" (storage-service deletes physically at once).
OLDEST_LOCK_HOURS = SensorSpec(
    name="document.locks.oldest_age_hours",
    group="reliability",
    cost=2,
    description="Alter der aeltesten aktiven Ausleih-Sperre in Stunden (0 ohne Sperren)",
)
TRASH_OVERDUE = SensorSpec(
    name="document.trash.overdue",
    group="reliability",
    cost=2,
    description=(
        "Papierkorb-Eintraege ueber der Wiederherstellungsfrist, noch nicht endgueltig "
        "geloescht (Holds nicht abgezogen)"
    ),
)


# P88-S2c (package E): a breakdown with one series per object type - level 5
# and off by default; switched on for a capacity question, not kept running.
COUNT_BY_TYPE = SensorSpec(
    name="document.count.by_type",
    group="capacity",
    cost=5,
    description="Aktive Dokumente je Objekttyp (eine Zeitreihe je Typ)",
    default_enabled=False,
)


def build_scrape_sensors(registry: SensorRegistry, app) -> None:
    """Pull-on-scrape (P88-S2b); `app` is read at scrape time."""

    async def oldest_lock_hours() -> float:
        now = datetime.now(UTC)
        async with app.state.session_factory() as session:
            oldest = await session.scalar(
                select(func.min(DocumentLock.locked_at)).where(DocumentLock.expires_at > now)
            )
        return 0.0 if oldest is None else (now - oldest).total_seconds() / 3600

    async def trash_overdue() -> float:
        async with app.state.session_factory() as session:
            config = await repository.get_trash_config(session)
            deadline = datetime.now(UTC) - timedelta(days=config.restore_period_days)
            count = await session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.deleted_at.isnot(None), Document.deleted_at <= deadline)
            )
        return float(count or 0)

    async def count_by_type() -> dict[tuple[str, ...], float]:
        async with app.state.session_factory() as session:
            rows = await session.execute(
                select(Document.object_type_id, func.count())
                .where(Document.deleted_at.is_(None))
                .group_by(Document.object_type_id)
            )
        return {
            ("none" if type_id is None else str(type_id),): float(n) for type_id, n in rows.all()
        }

    registry.on_scrape_async(
        registry.gauge(COUNT_BY_TYPE, labelnames=("object_type_id",)), count_by_type
    )
    registry.on_scrape_async(registry.gauge(OLDEST_LOCK_HOURS), oldest_lock_hours)
    registry.on_scrape_async(registry.gauge(TRASH_OVERDUE), trash_overdue)


def build_sensor_registry(registry: SensorRegistry) -> tuple[GuardedHistogram, GuardedGauge]:
    upload_duration = registry.histogram(UPLOAD_DURATION)
    active_documents = registry.gauge(ACTIVE_DOCUMENTS)
    return upload_duration, active_documents


def build_samplers(
    active_documents_gauge: GuardedGauge,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, tuple[GuardedGauge, Callable[[], Awaitable[float]]]]:
    async def count_active() -> float:
        async with session_factory() as session:
            return float(await repository.count_active_total(session))

    return {ACTIVE_DOCUMENTS.name: (active_documents_gauge, count_active)}
