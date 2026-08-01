import httpx


class NotFoundError(Exception):
    pass


class LockConflictError(Exception):
    pass


class DocumentServiceClient:
    """HTTP-Client gegen den Document Service (kein Mocking von
    Sibling-Services, siehe PROGRESS.md "Tooling & Testing"): lädt die zu
    signierende Version, checkt die signierten Bytes anschließend als neue
    Dokumentversion ein (2.1a) - siehe docs/services/signature-service.md
    "Signaturebene ↔ Dokumentversion"."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_document(self, document_id: str) -> dict:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            raise NotFoundError(f"document_id {document_id!r} unbekannt")
        response.raise_for_status()
        return response.json()

    async def get_version_content(
        self, document_id: str, version_number: int
    ) -> tuple[str | None, bytes]:
        meta_response = await self._client.get(
            f"/documents/{document_id}/versions/{version_number}"
        )
        if meta_response.status_code == 404:
            raise NotFoundError(f"document_id {document_id!r} Version {version_number} unbekannt")
        meta_response.raise_for_status()
        content_type = meta_response.json()["content_type"]

        content_response = await self._client.get(
            f"/documents/{document_id}/versions/{version_number}/content"
        )
        if content_response.status_code == 404:
            raise NotFoundError(f"document_id {document_id!r} Version {version_number} unbekannt")
        content_response.raise_for_status()
        return content_type, content_response.content

    async def checkin_signed_version(
        self,
        document_id: str,
        *,
        expected_base_version_number: int,
        signed_bytes: bytes,
        filename: str,
        created_by: str,
        comment: str,
    ) -> dict:
        response = await self._client.post(
            f"/documents/{document_id}/versions",
            data={
                "expected_base_version_number": str(expected_base_version_number),
                "created_by": created_by,
                "comment": comment,
            },
            files={"file": (filename, signed_bytes, "application/pdf")},
        )
        if response.status_code == 404:
            raise NotFoundError(f"document_id {document_id!r} unbekannt")
        if response.status_code == 409:
            raise LockConflictError(response.json().get("detail", "Dokument ist gesperrt"))
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
