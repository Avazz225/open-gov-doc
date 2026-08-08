import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import NatsEventBusClient, SubjectNotFoundError

from registry_service.licensing import ComponentLicenseCache

logger = logging.getLogger(__name__)


def make_handler(cache: ComponentLicenseCache) -> Callable[[bytes], Awaitable[None]]:
    """Erster NATS-Konsument von `registry-service` (Konzept 9.2: der License
    Service publiziert Statusaenderungen, die Registry konsumiert sie). Der
    Payload-Inhalt ist irrelevant - jedes `license.*`-Event bedeutet nur
    "beim naechsten Abfragen neu von license-service holen"."""

    async def handle(_payload: bytes) -> None:
        cache.invalidate()

    return handle


async def start_consuming(
    bus: NatsEventBusClient, subjects: list[str], cache: ComponentLicenseCache
) -> None:
    handler = make_handler(cache)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="registry-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream fuer Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum naechsten Neustart nicht konsumiert.",
                subject,
            )
