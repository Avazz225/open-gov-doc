import httpx


class DocumentClient:
    """HTTP-Client gegen document-service (2.1/2.1a) - liefert die aktuelle
    Hauptversionsnummer (`current_version_number`) sowie `deleted_at` fuer
    die dynamische Referenzaufloesung einer offenen Umlaufmappe (2.3).
    Ein weich geloeschtes Dokument bleibt ueber `GET /documents/{id}`
    weiterhin abrufbar (kein 404) - genau das deckt die Konzept-Anforderung
    "Referenz bleibt bei Loeschung des Originals nachvollziehbar bestehen"
    bereits ab, ohne eigene Logik hier."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get(self, document_id: str) -> dict | None:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
