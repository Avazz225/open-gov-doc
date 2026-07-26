import httpx


class ObjectTypeClient:
    """HTTP-Client gegen den Object-Type Service (2.2/4.5) - Folder Service
    ruft dessen `/validate`-Endpunkt auf, statt die Constraint-Engine-Lib
    selbst einzubinden (kein Import fremder Service-Interna)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def validate(self, object_type_id: int, *, name: str, attributes: dict) -> list[str]:
        response = await self._client.post(
            f"/object-types/{object_type_id}/validate",
            json={"name": name, "attributes": attributes},
        )
        response.raise_for_status()
        return response.json()["errors"]

    async def close(self) -> None:
        await self._client.aclose()
