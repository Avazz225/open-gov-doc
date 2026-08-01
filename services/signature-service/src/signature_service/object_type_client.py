import httpx


class NotFoundError(Exception):
    pass


class ObjectTypeServiceClient:
    """HTTP-Client gegen den Object-Type Service - fragt das je Objekttyp
    konfigurierbare Mindest-Signaturniveau ab (3.10, seit P6-S7:
    `required_signature_level`)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_required_signature_level(self, object_type_id: int) -> str | None:
        response = await self._client.get(f"/object-types/{object_type_id}")
        if response.status_code == 404:
            raise NotFoundError(f"object_type_id {object_type_id!r} unbekannt")
        response.raise_for_status()
        return response.json().get("required_signature_level")

    async def close(self) -> None:
        await self._client.aclose()
