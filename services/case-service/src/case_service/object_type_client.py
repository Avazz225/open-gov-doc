import httpx


class ObjectTypeClient:
    """HTTP-Client gegen den Object-Type Service (2.2) - identisches Muster
    wie document_service.object_type_client.ObjectTypeClient. Eine Umlaufmappe
    haengt konzeptionell nicht im Ordnerbaum (2.3), daher wird beim Validieren
    immer ein Wurzel-Objekt angenommen (kein `parent_object_type_id`)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def validate(self, object_type_id: int, *, name: str, attributes: dict) -> list[str]:
        response = await self._client.post(
            f"/object-types/{object_type_id}/validate",
            json={
                "name": name,
                "attributes": attributes,
                "parent_object_type_id": None,
                "parent_is_root": True,
            },
        )
        response.raise_for_status()
        return response.json()["errors"]

    async def close(self) -> None:
        await self._client.aclose()
