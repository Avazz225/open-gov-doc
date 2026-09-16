import httpx


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
