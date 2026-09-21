import httpx


class DocumentServiceClient:
    """HTTP client against the Document Service (P32-S2) - fallback
    resolution path for `_resolve_business_key_scope` alongside
    `CaseServiceClient`: the office-addin/libreoffice-addin "start workflow
    from this document" feature sets `business_key=documentId` for any
    process definition the user picks, live since those features shipped
    (an earlier version of this docstring claimed "no real process type
    sets this yet" - false, corrected in P66-S2). The field is genuinely
    opaque per ADR 0048's own framing ("a document or a circulation folder,
    depending on the process") - this activates `scope_folder_resource_ids`
    for document-keyed processes, reusing the already-existing
    `GET /documents/{id}` rather than adding new API surface.

    Post-Roadmap Phase 38 Session 4: `GET /documents/{id}` now requires a
    non-empty `X-DMS-Principal` header (previously ungated) - asserts a
    fixed service identity (background/internal caller, no real end-user
    context available; covered by "everyone"'s grants, same reasoning as
    `archival_service.clients.DocumentClient`)."""

    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "workflow-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def get_document(self, document_id: str) -> dict | None:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
