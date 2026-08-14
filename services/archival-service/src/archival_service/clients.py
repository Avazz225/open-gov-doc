import httpx


class DocumentClient:
    """Thin HTTP client against document-service (5.6) - document-service
    remains the sole authority for document lifecycle fields; this service
    only calls the internal endpoints provided for that purpose."""

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
    """Thin HTTP client against rendering-service - reads the `pdf_archive`
    rendition already produced by the regular rendition pipeline (P7-S3
    extension of `PdfArchiveRenderer`); does not itself trigger a new
    conversion.

    RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073): `GET /renditions`/
    `.../content` have since required `rendering.read` - both calls used
    here run as a pure machine-to-machine call without a human principal,
    the same `system:<Service>` pattern as `CaseClient` below."""

    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "system:archival-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

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
    """Thin HTTP client against storage-service - both the regular live
    target (retrieval, `upload`) and the new archive-target endpoints
    (P7-S3)."""

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
    """Thin HTTP client against object-type-service - only for looking up
    `archive_encryption_enabled` per object type."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_object_type(self, object_type_id: int) -> dict:
        response = await self._client.get(f"/object-types/{object_type_id}")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class CaseClient:
    """Thin HTTP client against case-service (5.6, since P7-S3b) - for the
    XDOMEA records disposal of circulation folders. case-service remains
    the sole authority for case lifecycle fields, the same principle as
    `DocumentClient` toward document-service.

    RBAC (Post-Roadmap Phase 19 Session 5, ADR 0070): since this session,
    case-service checks `case.read`/`case.write` and requires an
    `X-DMS-Principal` header for that. The read calls here (`get_case`,
    `list_document_references`, `get_archival_config`) run as a pure
    machine-to-machine call without a human principal - a synthetic
    identifier following the same "system:<Service>" pattern used elsewhere
    in the project (e.g. `actor="system:archival-service"` on published
    events). `list_due_for_archival`/`mark_archived` deliberately remain
    without the header - case-service leaves exactly these two endpoints
    ungated, unchanged (see the docstrings there)."""

    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "system:archival-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def list_due_for_archival(self) -> list[dict]:
        response = await self._client.get("/cases/due-for-archival")
        response.raise_for_status()
        return response.json()

    async def get_case(self, case_id: str) -> dict:
        response = await self._client.get(
            f"/cases/{case_id}", headers=self._SYSTEM_PRINCIPAL_HEADERS
        )
        response.raise_for_status()
        return response.json()

    async def list_document_references(self, case_id: str) -> list[dict]:
        response = await self._client.get(
            f"/cases/{case_id}/documents", headers=self._SYSTEM_PRINCIPAL_HEADERS
        )
        response.raise_for_status()
        return response.json()

    async def mark_archived(self, case_id: str) -> dict:
        response = await self._client.put(f"/cases/{case_id}/archived")
        response.raise_for_status()
        return response.json()

    async def get_archival_config(self) -> dict:
        response = await self._client.get(
            "/case-archival-config", headers=self._SYSTEM_PRINCIPAL_HEADERS
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
