import httpx
from dms_common import on_behalf_headers


class NotFoundError(Exception):
    pass


class LockConflictError(Exception):
    pass


class DocumentServiceClient:
    """HTTP client against the Document Service (no mocking of sibling
    services, see PROGRESS.md "Tooling & Testing"): loads the version to be
    signed, then checks the signed bytes in as a new document version
    (2.1a) - see docs/services/signature-service.md "Signature level ↔
    document version"."""

    # Post-Roadmap Phase 38 Session 4: document-service's primary endpoints
    # now require a non-empty `X-DMS-Principal` header - asserts a fixed
    # service identity (background/internal caller, no real end-user
    # context available; covered by "everyone"'s grants, same reasoning as
    # `archival_service.clients.DocumentClient`).
    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "signature-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def get_document(self, document_id: str) -> dict:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            raise NotFoundError(f"document_id {document_id!r} unbekannt")
        response.raise_for_status()
        return response.json()

    async def get_version_meta(self, document_id: str, version_number: int) -> dict:
        """Version metadata (content type, size) without the bytes - lets the
        incoming-signature check refuse an oversized file before download."""
        response = await self._client.get(f"/documents/{document_id}/versions/{version_number}")
        if response.status_code == 404:
            raise NotFoundError(f"document_id {document_id!r} Version {version_number} unbekannt")
        response.raise_for_status()
        return response.json()

    async def list_versions(
        self,
        *,
        created_from=None,
        created_to=None,
        folder_id: str | None = None,
        content_type: str | None = None,
        after_id: int = 0,
        limit: int = 500,
    ) -> list[dict]:
        """`GET /document-versions` (service principals only) - the backfill's
        source (P92-S2)."""
        params: dict = {"after_id": after_id, "limit": limit}
        if created_from is not None:
            params["created_from"] = created_from.isoformat()
        if created_to is not None:
            params["created_to"] = created_to.isoformat()
        if folder_id is not None:
            params["folder_id"] = folder_id
        if content_type is not None:
            params["content_type"] = content_type
        response = await self._client.get("/document-versions", params=params)
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
                "comment": comment,
            },
            files={"file": (filename, signed_bytes, "application/pdf")},
            # P85-S4 (ADR 0240): checked in on behalf of the signer.
            headers=on_behalf_headers("signature-service", created_by),
        )
        if response.status_code == 404:
            raise NotFoundError(f"document_id {document_id!r} unbekannt")
        if response.status_code == 409:
            raise LockConflictError(response.json().get("detail", "Dokument ist gesperrt"))
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
