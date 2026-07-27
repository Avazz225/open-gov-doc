from dataclasses import dataclass

import httpx


class DocumentNotFoundError(Exception):
    """Dokument/Version wurde beim Document Service nicht (mehr) gefunden -
    z. B. inzwischen gelöscht, während das Event noch in der Zustellung war."""


@dataclass
class VersionMetadata:
    filename: str
    content_type: str | None


class DocumentServiceClient:
    """HTTP-Client gegen den Document Service (3.1: eigenes Schema pro Service,
    kein direkter DB-/Storage-Key-Zugriff). Der OCR Service kennt weder das
    interne Datenmodell noch den content-adressierten Storage-Key einer
    Version - beides bleibt Sache des Document Service, Original-Metadaten und
    -Inhalt werden ausschließlich über dessen öffentliche API bezogen."""

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
