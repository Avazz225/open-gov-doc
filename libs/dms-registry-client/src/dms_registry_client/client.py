import asyncio
import contextlib
import logging
import uuid

import httpx

logger = logging.getLogger(__name__)


class RegistryRegistration:
    """Selbst-Registrierung eines Service bei der Registry (3.2a, seit P4-S1):
    meldet sich beim Start an, schickt periodisch Heartbeats und meldet sich
    beim Shutdown sauber ab.

    Discovery ist ein Zusatznutzen (Grundlage für das Gateway-Routing, 3.5),
    kein Hard-Dependency für den registrierenden Service selbst - Fehler beim
    Erreichen der Registry werden geloggt, aber nicht weitergeworfen.
    """

    def __init__(
        self,
        *,
        registry_base_url: str,
        service_type: str,
        version: str,
        address: str,
        health_endpoint: str = "/healthz",
        capabilities: list[str] | None = None,
        heartbeat_interval_seconds: float = 10.0,
        instance_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._instance_id = instance_id or f"{service_type}-{uuid.uuid4().hex[:8]}"
        self._payload = {
            "instance_id": self._instance_id,
            "service_type": service_type,
            "version": version,
            "capabilities": capabilities or [],
            "health_endpoint": health_endpoint,
            "address": address,
        }
        self._heartbeat_interval = heartbeat_interval_seconds
        self._client = client or httpx.AsyncClient(
            base_url=registry_base_url.rstrip("/"), timeout=5.0
        )
        self._task: asyncio.Task | None = None

    @property
    def instance_id(self) -> str:
        return self._instance_id

    async def start(self) -> None:
        try:
            await self._client.post("/instances", json=self._payload)
        except httpx.HTTPError:
            logger.warning("registry_registration_failed", extra={"instance_id": self._instance_id})
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        # Selbstheilend: schlägt der Heartbeat mit 404 fehl (Instanz unbekannt -
        # z. B. weil die allererste Registrierung lief, bevor die Registry beim
        # Container-Start schon erreichbar war), wird erneut vollständig
        # registriert (Upsert) statt für immer gegen eine nie existierende
        # Instanz zu heartbeaten.
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                response = await self._client.post(f"/instances/{self._instance_id}/heartbeat")
                if response.status_code == 404:
                    await self._client.post("/instances", json=self._payload)
            except httpx.HTTPError:
                logger.warning(
                    "registry_heartbeat_failed", extra={"instance_id": self._instance_id}
                )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        try:
            await self._client.delete(f"/instances/{self._instance_id}")
        except httpx.HTTPError:
            logger.warning(
                "registry_deregistration_failed", extra={"instance_id": self._instance_id}
            )
        await self._client.aclose()


async def maybe_start_registration(
    *,
    registry_service_base_url: str | None,
    self_address: str | None,
    service_type: str,
    version: str,
    health_endpoint: str = "/healthz",
    capabilities: list[str] | None = None,
    heartbeat_interval_seconds: float = 10.0,
) -> RegistryRegistration | None:
    """Baut und startet eine `RegistryRegistration`, sofern sowohl die
    Registry-URL als auch die eigene erreichbare Adresse konfiguriert sind
    (siehe `BaseServiceSettings.registry_service_base_url`/`self_address`).
    Sonst `None` - der Service läuft unverändert ohne Discovery.
    """
    if not registry_service_base_url or not self_address:
        return None
    registration = RegistryRegistration(
        registry_base_url=registry_service_base_url,
        service_type=service_type,
        version=version,
        address=self_address,
        health_endpoint=health_endpoint,
        capabilities=capabilities,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    await registration.start()
    return registration
