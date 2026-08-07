import httpx


class DocumentClient:
    """Duenner HTTP-Client gegen document-service (5.6) - document-service
    bleibt alleinige Autoritaet fuer Dokument-Lebenszyklusfelder, dieser
    Service ruft nur die dafuer vorgesehenen internen Endpunkte auf."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def list_due_for_archival(self) -> list[dict]:
        response = await self._client.get("/documents/due-for-archival")
        response.raise_for_status()
        return response.json()

    async def get_document(self, document_id: str) -> dict:
        response = await self._client.get(f"/documents/{document_id}")
        response.raise_for_status()
        return response.json()

    async def get_version(self, document_id: str, version_number: int) -> dict:
        response = await self._client.get(f"/documents/{document_id}/versions/{version_number}")
        response.raise_for_status()
        return response.json()

    async def download_version_content(self, document_id: str, version_number: int) -> bytes:
        response = await self._client.get(
            f"/documents/{document_id}/versions/{version_number}/content"
        )
        response.raise_for_status()
        return response.content

    async def has_active_hold(self, document_id: str) -> bool:
        response = await self._client.get(f"/documents/{document_id}/has-active-hold")
        response.raise_for_status()
        return response.json()["has_active_hold"]

    async def mark_archived(self, document_id: str, *, archive_format: str) -> dict:
        response = await self._client.put(
            f"/documents/{document_id}/archived", json={"archive_format": archive_format}
        )
        response.raise_for_status()
        return response.json()

    async def mark_dehydrated(self, document_id: str) -> dict:
        response = await self._client.put(f"/documents/{document_id}/dehydrated")
        response.raise_for_status()
        return response.json()

    async def mark_rehydrated(self, document_id: str) -> dict:
        response = await self._client.put(f"/documents/{document_id}/rehydrated")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class RenderingClient:
    """Duenner HTTP-Client gegen rendering-service - liest die bereits vom
    regulaeren Rendition-Pipeline erzeugte `pdf_archive`-Ersatzdarstellung
    (P7-S3-Erweiterung von `PdfArchiveRenderer`), triggert selbst keine neue
    Konvertierung."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_pdf_archive_rendition(
        self, *, document_id: str, version_number: int
    ) -> dict | None:
        response = await self._client.get(
            "/renditions", params={"document_id": document_id, "version_number": version_number}
        )
        response.raise_for_status()
        renditions = response.json()
        return next((r for r in renditions if r["rendition_type"] == "pdf_archive"), None)

    async def download_rendition_content(self, rendition_id: str) -> bytes:
        response = await self._client.get(f"/renditions/{rendition_id}/content")
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()


class StorageClient:
    """Duenner HTTP-Client gegen storage-service - sowohl das reguläre
    Live-Ziel (Rueckholung, `upload`) als auch die neuen Archiv-Ziel-
    Endpunkte (P7-S3)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0)

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        response = await self._client.put(
            f"/objects/{key}", content=data, headers={"Content-Type": content_type}
        )
        response.raise_for_status()

    async def upload_archive_copy(self, key: str, data: bytes, content_type: str) -> None:
        response = await self._client.put(
            f"/objects/{key}/archive-copy", content=data, headers={"Content-Type": content_type}
        )
        response.raise_for_status()

    async def verify_archive_copy(self, key: str) -> list[dict]:
        response = await self._client.get(f"/objects/{key}/archive-copy/verify")
        response.raise_for_status()
        return response.json()

    async def download_archive_copy(self, key: str) -> bytes:
        response = await self._client.get(f"/objects/{key}/archive-copy")
        response.raise_for_status()
        return response.content

    async def delete_live_copies(self, key: str) -> None:
        response = await self._client.delete(f"/objects/{key}/live-copies")
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


class ObjectTypeClient:
    """Duenner HTTP-Client gegen object-type-service - nur zum Nachschlagen
    von `archive_encryption_enabled` pro Objekttyp."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_object_type(self, object_type_id: int) -> dict:
        response = await self._client.get(f"/object-types/{object_type_id}")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class CaseClient:
    """Duenner HTTP-Client gegen case-service (5.6, seit P7-S3b) - fuer die
    XDOMEA-Aussonderung von Umlaufmappen. case-service bleibt alleinige
    Autoritaet fuer Case-Lebenszyklusfelder, gleiches Prinzip wie
    `DocumentClient` gegenueber document-service."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def list_due_for_archival(self) -> list[dict]:
        response = await self._client.get("/cases/due-for-archival")
        response.raise_for_status()
        return response.json()

    async def get_case(self, case_id: str) -> dict:
        response = await self._client.get(f"/cases/{case_id}")
        response.raise_for_status()
        return response.json()

    async def list_document_references(self, case_id: str) -> list[dict]:
        response = await self._client.get(f"/cases/{case_id}/documents")
        response.raise_for_status()
        return response.json()

    async def mark_archived(self, case_id: str) -> dict:
        response = await self._client.put(f"/cases/{case_id}/archived")
        response.raise_for_status()
        return response.json()

    async def get_archival_config(self) -> dict:
        response = await self._client.get("/case-archival-config")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
