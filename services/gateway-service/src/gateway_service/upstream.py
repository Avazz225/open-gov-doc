import random
import time

import httpx

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "host",
    }
)


def filter_headers(headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


class InstanceResolver:
    """Fragt für jeden `service_type` die Registry nach aktiven Instanzen (3.2a)
    und cached das Ergebnis kurz (`instance_cache_ttl_seconds`), damit nicht
    jeder proxied Request einen zusätzlichen Registry-Roundtrip kostet.
    Schlägt der Registry-Aufruf fehl, wird der letzte bekannte Stand
    weiterverwendet statt sofort auf 503 zu gehen (kurzzeitige Registry-
    Ausfälle sollen laufenden Betrieb nicht sofort lahmlegen).
    """

    def __init__(
        self, *, client: httpx.AsyncClient, registry_base_url: str, cache_ttl_seconds: float
    ) -> None:
        self._client = client
        self._registry_base_url = registry_base_url.rstrip("/")
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    async def resolve(self, service_type: str) -> list[dict]:
        now = time.monotonic()
        cached = self._cache.get(service_type)
        if cached is not None and now - cached[0] < self._cache_ttl:
            return cached[1]

        try:
            response = await self._client.get(f"{self._registry_base_url}/instances/{service_type}")
            response.raise_for_status()
            # Drain-Mechanismus (10.5/3.8, P10-S2): eine "draining" Instanz
            # bleibt erreichbar (laufende Vorgaenge schliessen regulaer ab),
            # bekommt aber keine NEUEN Anfragen mehr - sie faellt hier aus
            # dem Auswahl-Pool, statt eigens abgefragt/gekillt zu werden.
            instances = [
                i for i in response.json() if i["healthy"] and i.get("status", "active") == "active"
            ]
        except httpx.HTTPError:
            instances = cached[1] if cached is not None else []

        self._cache[service_type] = (now, instances)
        return instances

    def pick(self, instances: list[dict]) -> dict:
        return random.choice(instances)


class MaintenanceStateClient:
    """Fragt den Wartungsmodus-Status (4.8, P6-S6) direkt bei `permission-service`
    ab - über denselben `InstanceResolver` wie jeder proxied Request, da das
    Gateway keine feste Service-URL kennt (alles läuft über die Registry).
    Kurzes Caching (`cache_ttl_seconds`) wie bei `InstanceResolver`, aus
    demselben Grund: nicht jeder einzelne Request soll einen zusätzlichen
    Roundtrip kosten. Schlägt die Abfrage fehl (`permission-service`
    unerreichbar), wird **nicht** aktiv angenommen (fail-open) - ein
    kurzzeitig unerreichbarer `permission-service` soll nicht versehentlich
    das gesamte System lahmlegen, das wäre ein Verfügbarkeits-Eigentor,
    keine Sicherheitsmaßnahme."""

    def __init__(
        self, *, client: httpx.AsyncClient, resolver: InstanceResolver, cache_ttl_seconds: float
    ) -> None:
        self._client = client
        self._resolver = resolver
        self._cache_ttl = cache_ttl_seconds
        self._cached_at: float = 0.0
        self._cached_active: bool = False

    async def is_active(self) -> bool:
        now = time.monotonic()
        if now - self._cached_at < self._cache_ttl:
            return self._cached_active

        try:
            instances = await self._resolver.resolve("permission-service")
            if not instances:
                return self._cached_active
            instance = self._resolver.pick(instances)
            response = await self._client.get(f"{instance['address'].rstrip('/')}/maintenance-mode")
            response.raise_for_status()
            self._cached_active = response.json()["active"]
        except httpx.HTTPError:
            pass
        self._cached_at = now
        return self._cached_active
