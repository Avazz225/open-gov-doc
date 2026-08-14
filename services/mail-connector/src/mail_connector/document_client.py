import httpx


class DocumentClient:
    """Thin HTTP client against the public document-service API - deliberately
    calls the REGULAR `POST /documents` path (not the internal quarantine
    release path from P15-S2): attachments here have already been checked
    separately via `VirusScanClient.scan()`, so a repeat scan by
    `POST /documents` itself is redundant but harmless (not a structural
    blocker like with quarantine release, where the same bytes already
    identified as infected would be resubmitted) - see ADR 0053."""

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
        """Returns the file metadata (`filename`/`content_type`/
        `storage_object_key`) of the current version - `DocumentOut` itself
        does not carry these, document-service versions content separately
        from the document record (`DocumentVersionOut`, see its own
        `schemas.py`). Used by the outbound-mail attachment path (P24-S3,
        `main.py`'s `_attach_related_document`)."""
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
