import httpx


class ObjectTypeClient:
    """Dünner HTTP-Client gegen object-type-service (Post-Roadmap Phase 19
    Session 11) - liest die je Objekttyp konfigurierten `kennzeichen_format`-
    Werte, aus denen `matching.py` das tatsächlich konfigurierte Kandidaten-
    Muster ableitet, statt eines fest kodierten generischen Musters.
    `GET /object-types` ist ungegated, kein Identitäts-Header nötig."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def list_kennzeichen_formats(self) -> list[str]:
        response = await self._client.get("/object-types")
        response.raise_for_status()
        formats = {
            object_type["kennzeichen_format"]
            for object_type in response.json()
            if object_type.get("kennzeichen_format")
        }
        return sorted(formats)

    async def close(self) -> None:
        await self._client.aclose()
