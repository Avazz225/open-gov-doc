import asyncio
import contextlib
import logging
import uuid

import httpx

logger = logging.getLogger(__name__)
# Set to WARN during development to reduce log output.
# Later, control logger granularity via XML or JSON config management.
logging.getLogger("httpx").setLevel(logging.WARNING)


class RegistryRegistration:
    """Self-registration of a service with the Registry (3.2a, since P4-S1):
    registers on startup, sends periodic heartbeats, and cleanly deregisters
    on shutdown.

    Discovery is an added benefit (the basis for gateway routing, 3.5), not a
    hard dependency for the registering service itself - errors reaching the
    Registry are logged but not re-raised.
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
        sensors: list[dict] | None = None,
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
            # Sensor catalog (10.1, P11-S1): which sensors this instance
            # offers - `monitoring-service` reads this via the already
            # existing `GET /instances` query against the Registry, no
            # second discovery channel.
            "sensors": sensors or [],
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
        # Self-healing: if the heartbeat fails with 404 (instance unknown -
        # e.g. because the very first registration ran before the Registry
        # was reachable at container startup), it re-registers fully
        # (upsert) instead of heartbeating forever against an instance that
        # never existed.
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
    sensors: list[dict] | None = None,
    heartbeat_interval_seconds: float = 10.0,
) -> RegistryRegistration | None:
    """Builds and starts a `RegistryRegistration`, provided both the
    Registry URL and the service's own reachable address are configured
    (see `BaseServiceSettings.registry_service_base_url`/`self_address`).
    Otherwise `None` - the service runs unchanged, without discovery.
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
        sensors=sensors,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    await registration.start()
    return registration
