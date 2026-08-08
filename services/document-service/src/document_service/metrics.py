from collections.abc import Awaitable, Callable

from dms_metrics_client import (
    GuardedGauge,
    GuardedHistogram,
    SensorConfigClient,
    SensorRegistry,
    SensorSpec,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from document_service import repository

# document-service ist einer der zwei Sensor-Piloten (10.1, P11-S1, siehe
# P11-S0-Befund: kein Vollretrofit aller Services). Beide Sensor-Namen sind
# bewusst wörtlich aus Konzept 10.1s eigener Beispielliste übernommen.
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
    return [UPLOAD_DURATION.as_dict(), ACTIVE_DOCUMENTS.as_dict()]


def build_sensor_registry(
    config_client: SensorConfigClient,
) -> tuple[SensorRegistry, GuardedHistogram, GuardedGauge]:
    registry = SensorRegistry("document-service", is_active=config_client.is_active)
    upload_duration = registry.histogram(UPLOAD_DURATION)
    active_documents = registry.gauge(ACTIVE_DOCUMENTS)
    return registry, upload_duration, active_documents


def build_samplers(
    active_documents_gauge: GuardedGauge,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, tuple[GuardedGauge, Callable[[], Awaitable[float]]]]:
    async def count_active() -> float:
        async with session_factory() as session:
            return float(await repository.count_active_total(session))

    return {ACTIVE_DOCUMENTS.name: (active_documents_gauge, count_active)}
