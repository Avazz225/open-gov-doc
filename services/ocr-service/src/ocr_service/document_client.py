from dataclasses import dataclass

import httpx


class DocumentNotFoundError(Exception):
    """Document/version was not (or no longer) found at the Document Service
    - e.g. deleted in the meantime while the event was still in delivery."""


@dataclass
class VersionMetadata:
    filename: str
    content_type: str | None
    created_by: str


class DocumentServiceClient:
    """HTTP client against the Document Service (3.1: separate schema per
    service, no direct DB/storage key access). The OCR Service knows neither
    the internal data model nor the content-addressed storage key of a
    version - both remain the Document Service's responsibility; original
    metadata and content are obtained exclusively via its public API."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_version(self, document_id: str, version_number: int) -> VersionMetadata:
        response = await self._client.get(f"/documents/{document_id}/versions/{version_number}")
        if response.status_code == 404:
            raise DocumentNotFoundError(f"{document_id!r} Version {version_number} unbekannt")
        response.raise_for_status()
        body = response.json()
        return VersionMetadata(
            filename=body["filename"],
            content_type=body.get("content_type"),
            created_by=body["created_by"],
        )

    async def download_content(self, document_id: str, version_number: int) -> bytes:
        response = await self._client.get(
            f"/documents/{document_id}/versions/{version_number}/content"
        )
        if response.status_code == 404:
            raise DocumentNotFoundError(f"{document_id!r} Version {version_number} unbekannt")
        response.raise_for_status()
        return response.content

    async def create_version(
        self,
        document_id: str,
        *,
        expected_base_version_number: int,
        data: bytes,
        filename: str,
        content_type: str,
        created_by: str,
        comment: str,
    ) -> dict:
        """Checks in a file modified by the OCR Service (text layer
        embedding, see text_layer.py) as a new document version - identical
        pattern to
        signature_service.document_client.checkin_signed_version() (ADR
        0025: processing that changes the bytes creates a new version
        server-side instead of overwriting the original version)."""
        response = await self._client.post(
            f"/documents/{document_id}/versions",
            data={
                "expected_base_version_number": str(expected_base_version_number),
                "created_by": created_by,
                "comment": comment,
            },
            files={"file": (filename, data, content_type)},
        )
        if response.status_code == 404:
            raise DocumentNotFoundError(f"{document_id!r} unbekannt")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
