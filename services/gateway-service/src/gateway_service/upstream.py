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
            instances = [i for i in response.json() if i["healthy"]]
        except httpx.HTTPError:
            instances = cached[1] if cached is not None else []

        self._cache[service_type] = (now, instances)
        return instances

    def pick(self, instances: list[dict]) -> dict:
        return random.choice(instances)
