from collections.abc import Awaitable, Callable

from dms_metrics_client import (
    GuardedGauge,
    GuardedHistogram,
    SensorRegistry,
    SensorSpec,
    http_sensor_declarations,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from document_service import repository

# document-service was one of the two original sensor pilots (10.1,
# P11-S1, see P11-S0 finding) - both bespoke sensor names below are
# deliberately taken verbatim from concept 10.1's own example list. Now
# additionally covered by the generic http.* sensors from the full rollout
# (see `main.py`'s module-level `bootstrap_http_sensors` call, which builds
# the `SensorRegistry` this module's sensors are added to).
UPLOAD_DURATION = SensorSpec(
    name="document.upload.duration",
    group="performance",
    cost="expensive",
    description="Dauer eines Dokument-Uploads (Virenscan + Storage-Schreibvorgang)",
)
ACTIVE_DOCUMENTS = SensorSpec(
    name="document.count.active_total",
    group="capacity",
    cost="cheap",
    description="Anzahl installationsweit aktiver Dokumente",
)


def sensor_declarations() -> list[dict]:
    return [UPLOAD_DURATION.as_dict(), ACTIVE_DOCUMENTS.as_dict(), *http_sensor_declarations()]


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
