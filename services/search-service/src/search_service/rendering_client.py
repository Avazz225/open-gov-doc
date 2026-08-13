import httpx


class RenderingServiceClient:
    """HTTP-Client gegen den Rendering Service (3.7, P5-S2) - Fallback-
    Volltextquelle, falls kein OCR-Ergebnis vorliegt (siehe consumer.py).
    `GET /renditions` filtert nicht nach `rendition_type` - Filterung passiert
    hier client-seitig."""

    # RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - `GET /renditions`/
    # `.../content` verlangen seither `rendering.read`; dieser Aufruf läuft
    # aus einem NATS-Consumer heraus, kein menschlicher Principal verfügbar -
    # gleiches `system:<Service>`-Muster wie `archival-service`s `CaseClient`.
    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "system:search-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def get_substitute_text(self, document_id: str, version_number: int) -> str | None:
        response = await self._client.get(
            "/renditions", params={"document_id": document_id, "version_number": version_number}
        )
        response.raise_for_status()
        renditions = response.json()
        for rendition in renditions:
            if rendition["rendition_type"] == "substitute_text" and rendition["status"] == "ready":
                content_response = await self._client.get(f"/renditions/{rendition['id']}/content")
                content_response.raise_for_status()
                return content_response.content.decode("utf-8", errors="replace")
        return None

    async def close(self) -> None:
        await self._client.aclose()
