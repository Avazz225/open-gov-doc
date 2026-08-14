import httpx


class DocumentClient:
    """Dünner HTTP-Client gegen die öffentliche Document-Service-API - ruft
    bewusst den REGULÄREN `POST /documents`-Pfad auf (nicht den internen
    Quarantäne-Freigabe-Pfad aus P15-S2): Anhänge wurden hier bereits über
    `VirusScanClient.scan()` separat geprüft, ein erneuter Scan durch
    `POST /documents` selbst ist redundant, aber harmlos (kein struktureller
    Blocker wie bei der Quarantäne-Freigabe, wo dieselben, bereits als
    infiziert erkannten Bytes erneut vorgelegt würden) - siehe ADR 0053."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def create_document(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        title: str,
        created_by: str,
        folder_id: str | None,
    ) -> dict:
        form: dict = {"title": title, "created_by": created_by}
        if folder_id is not None:
            form["folder_id"] = folder_id
        response = await self._client.post(
            "/documents",
            data=form,
            files={"file": (filename, data, content_type or "application/octet-stream")},
        )
        response.raise_for_status()
        return response.json()

    async def get(self, document_id: str) -> dict | None:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def get_current_version(self, document_id: str) -> dict | None:
        """Liefert die Datei-Metadaten (`filename`/`content_type`/
        `storage_object_key`) der aktuellen Version - `DocumentOut` selbst
        trägt diese nicht, document-service versioniert Inhalte getrennt vom
        Dokument-Datensatz (`DocumentVersionOut`, siehe dortiges
        `schemas.py`). Genutzt vom Postausgang-Anhang-Pfad (P24-S3,
        `main.py`s `_attach_related_document`)."""
        document = await self.get(document_id)
        if document is None:
            return None
        response = await self._client.get(
            f"/documents/{document_id}/versions/{document['current_version_number']}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def lookup_by_kennzeichen(self, value: str) -> list[dict]:
        response = await self._client.get("/documents/by-kennzeichen", params={"value": value})
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
