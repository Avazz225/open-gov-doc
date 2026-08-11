import httpx


class MissingKennzeichenAttributeError(Exception):
    """Ein im `kennzeichen_format` referenzierter Attribut-Platzhalter
    (P17-S2, 14.2) hat beim Anlegen keinen Wert erhalten - object-type-service
    liefert dafür `422`, siehe dortiges `repository.MissingKennzeichenAttributeError`."""


class ObjectTypeClient:
    """HTTP-Client gegen den Object-Type Service (2.2/4.5) - Document Service
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
        """Aufbewahrung (5.2, seit P7-S1): liest `default_retention_days`/
        `deletion_reason_required_override` für den Objekttyp - `None` bei
        unbekannter `object_type_id` statt eines Fehlers, da der Aufrufer die
        Existenz an dieser Stelle bereits über `validate()` geprüft hat."""
        response = await self._client.get(f"/object-types/{object_type_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def next_kennzeichen(
        self, object_type_id: int, attributes: dict | None = None
    ) -> str | None:
        """Kennzeichengenerator (2.2, P5e-S2, seit P17-S2 mit Attributwerten
        für attributbasierte Platzhalter wie `{Federführung}`, 14.2) - `None`
        bedeutet, dass für diese Dokumentklasse kein Generator konfiguriert
        ist (404), nicht dass der Objekttyp selbst unbekannt wäre (der wurde
        bereits über `validate()` geprüft, bevor dieser Aufruf erfolgt)."""
        response = await self._client.post(
            f"/object-types/{object_type_id}/next-kennzeichen",
            json={"attributes": attributes or {}},
        )
        if response.status_code == 404:
            return None
        if response.status_code == 422:
            raise MissingKennzeichenAttributeError(response.json().get("detail", response.text))
        response.raise_for_status()
        return response.json()["kennzeichen"]

    async def list_classified_document_type_ids(self) -> set[int]:
        """Verschlusssachen-Papierkorb (2.5, P15-S1) - liefert die IDs aller
        als Verschlusssache eingestuften Dokument-Objekttypen, damit
        document-service den Papierkorb strukturell trennen kann, ohne pro
        gelöschtem Dokument einen einzelnen `get()`-Aufruf zu machen."""
        response = await self._client.get(
            "/object-types", params={"applies_to": "document", "is_classified": "true"}
        )
        response.raise_for_status()
        return {row["id"] for row in response.json()}

    async def close(self) -> None:
        await self._client.aclose()
