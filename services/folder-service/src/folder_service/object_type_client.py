import httpx


class ObjectTypeClient:
    """HTTP-Client gegen den Object-Type Service (2.2/4.5) - Folder Service
    ruft dessen `/validate`-Endpunkt auf, statt die Constraint-Engine-Lib
    selbst einzubinden (kein Import fremder Service-Interna)."""

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
        """Aufbewahrung (5.2, seit P7-S1b): liest `default_retention_days`/
        `deletion_reason_required_override` für den Objekttyp - `None` bei
        unbekannter `object_type_id` statt eines Fehlers, da der Aufrufer die
        Existenz an dieser Stelle bereits über `validate()` geprüft hat.
        Identisches Muster wie `document_service.object_type_client` (P7-S1)."""
        response = await self._client.get(f"/object-types/{object_type_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
