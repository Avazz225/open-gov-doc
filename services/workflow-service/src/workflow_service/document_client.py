import httpx


class DocumentServiceClient:
    """HTTP client against the Document Service (P32-S2) - fallback
    resolution path for `_resolve_business_key_scope` alongside
    `CaseServiceClient`: no real process type sets `business_key` to a
    document ID today (`ProcessInstance.business_key`'s own docstring only
    aspirationally names "a future document_id"), but the field is
    genuinely opaque per ADR 0048's own framing ("a document or a
    circulation folder, depending on the process") - this activates
    `scope_folder_resource_ids` for the day a document-keyed process type
    exists, reusing the already-existing `GET /documents/{id}` rather than
    adding new API surface. `GET /documents/{id}` has no RBAC gate of its
    own (see `document_service.main`), so no principal header is needed."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_document(self, document_id: str) -> dict | None:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
