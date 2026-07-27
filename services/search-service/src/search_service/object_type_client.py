from typing import Any

import httpx


class ObjectTypeServiceClient:
    """HTTP-Client gegen den Object-Type Service (3.1) - reiner
    Referenzdaten-Dienst ohne eigene Events (bestätigt in dessen Doku), daher
    wird das Attributschema hier synchron abgefragt statt reaktiv aktualisiert.
    Nötig für die Facetten-Definition (`GET /search/facets`) und die
    Typ-Verzweigung der Attributfilter (exakt vs. Bereich)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get(self, object_type_id: int) -> dict[str, Any] | None:
        response = await self._client.get(f"/object-types/{object_type_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def list(self) -> list[dict[str, Any]]:
        response = await self._client.get("/object-types")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
