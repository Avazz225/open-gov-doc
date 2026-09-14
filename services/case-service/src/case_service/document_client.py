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

    async def has_active_quarantine(self, document_id: str) -> bool:
        """Records quarantine (14.2, ADR 0116) - surfaces existing document-
        level quarantine status at the case level (Post-Roadmap Phase 36
        Session 2), since case-service has no destruction-scheduling
        primitive of its own to hook a genuine "case quarantine" into (same
        "no realistic entry point" conclusion ADR 0115/0118 already reached
        for redaction/hand-folders). Deliberately the ungated `GET .../has-
        active-quarantine` (a document's own status isn't restricted-
        visibility content, only bulk listing is) - safe to call for every
        reference resolved via `GET /cases/{id}/documents`, same trust
        level as `get()` above. `404` (unknown document) is treated as
        `False`."""
        response = await self._client.get(f"/documents/{document_id}/has-active-quarantine")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return response.json()["has_active_quarantine"]

    async def close(self) -> None:
        await self._client.aclose()
