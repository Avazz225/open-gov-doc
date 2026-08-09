from dataclasses import dataclass

import httpx


class DocumentNotFoundError(Exception):
    """Dokument/Version wurde beim Document Service nicht (mehr) gefunden -
    z. B. inzwischen gelöscht, während das Event noch in der Zustellung war."""


@dataclass
class VersionMetadata:
    filename: str
    content_type: str | None
    created_by: str


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
        """Checkt eine vom OCR Service veränderte Datei (Textlayer-Einbettung,
        siehe text_layer.py) als neue Dokumentversion ein - identisches Muster
        wie signature_service.document_client.checkin_signed_version() (ADR
        0025: Verarbeitung, die die Bytes verändert, erzeugt serverseitig eine
        neue Version statt die Originalversion zu überschreiben)."""
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
