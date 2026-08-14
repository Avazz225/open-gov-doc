import httpx


class ObjectTypeClient:
    """HTTP client against the Object-Type Service (2.2/4.5) - Folder
    Service calls its `/validate` endpoint instead of embedding the
    constraint-engine library itself (no import of another service's
    internals)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def validate(
        self,
        object_type_id: int,
        *,
        name: str,
        attributes: dict,
        parent_object_type_id: int | None = None,
        parent_is_root: bool = False,
    ) -> list[str]:
        response = await self._client.post(
            f"/object-types/{object_type_id}/validate",
            json={
                "name": name,
                "attributes": attributes,
                "parent_object_type_id": parent_object_type_id,
                "parent_is_root": parent_is_root,
            },
        )
        response.raise_for_status()
        return response.json()["errors"]

    async def get(self, object_type_id: int) -> dict | None:
        """Retention (5.2, since P7-S1b): reads `default_retention_days`/
        `deletion_reason_required_override` for the object type - `None` for
        an unknown `object_type_id` instead of an error, since the caller
        has already checked existence at this point via `validate()`.
        Identical pattern to `document_service.object_type_client` (P7-S1)."""
        response = await self._client.get(f"/object-types/{object_type_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
