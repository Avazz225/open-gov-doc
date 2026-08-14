from dataclasses import dataclass

import httpx


class DocumentNotFoundError(Exception):
    """Document/version was not (or no longer) found at the Document Service -
    e.g. deleted in the meantime while the rendering event was still in
    delivery."""


@dataclass
class VersionMetadata:
    filename: str
    content_type: str | None


class DocumentServiceClient:
    """HTTP client against the Document Service (3.1: own schema per service,
    no direct DB/storage key access). The Rendering Service knows neither
    the internal data model nor the content-addressed storage key of a
    version - both remain the Document Service's responsibility, original
    metadata and content are obtained exclusively via its public API."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_version(self, document_id: str, version_number: int) -> VersionMetadata:
        response = await self._client.get(f"/documents/{document_id}/versions/{version_number}")
        if response.status_code == 404:
            raise DocumentNotFoundError(f"{document_id!r} Version {version_number} unbekannt")
        response.raise_for_status()
        body = response.json()
        return VersionMetadata(filename=body["filename"], content_type=body.get("content_type"))

    async def download_content(self, document_id: str, version_number: int) -> bytes:
        response = await self._client.get(
            f"/documents/{document_id}/versions/{version_number}/content"
        )
        if response.status_code == 404:
            raise DocumentNotFoundError(f"{document_id!r} Version {version_number} unbekannt")
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()
