import httpx


class CaseClient:
    """Dünner HTTP-Client gegen die case-service-API - Vorgangsnummer-
    Zuordnungssuche (P15-S3) und Ergänzen der bei einer Fall-Zuordnung neu
    angelegten Dokumente als Referenz auf die getroffene Umlaufmappe (2.3,
    bereits bestehender Endpunkt, keine Erweiterung nötig)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def lookup_by_vorgangsnummer(self, value: str) -> list[dict]:
        response = await self._client.get("/cases/by-vorgangsnummer", params={"value": value})
        response.raise_for_status()
        return response.json()

    async def get(self, case_id: str) -> dict | None:
        response = await self._client.get(f"/cases/{case_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def add_document_reference(
        self, case_id: str, *, document_id: str, added_by: str
    ) -> None:
        response = await self._client.post(
            f"/cases/{case_id}/documents",
            json={"document_id": document_id, "added_by": added_by},
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
