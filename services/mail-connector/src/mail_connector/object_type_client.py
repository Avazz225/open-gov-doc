import httpx


class ObjectTypeClient:
    """Thin HTTP client against object-type-service (Post-Roadmap Phase 19
    Session 11) - reads the `kennzeichen_format` (reference number format)
    values configured per object type, from which `matching.py` derives the
    actually configured candidate pattern, instead of a hard-coded generic
    pattern. `GET /object-types` is ungated, no identity header needed."""

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
