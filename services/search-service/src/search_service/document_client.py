from typing import Any

import httpx


class DocumentServiceClient:
    """HTTP client against the Document Service (3.1). Document events are
    deliberately thin (see consumer.py) - the full record is reloaded here
    on every event, instead of relying on event payload fields."""

    # Post-Roadmap Phase 38 Session 4: document-service's primary endpoints
    # now require a non-empty `X-DMS-Principal` header - asserts a fixed
    # service identity (background/internal caller, no real end-user
    # context available; covered by "everyone"'s grants, same reasoning as
    # `archival_service.clients.DocumentClient`).
    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "search-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def get(self, document_id: str) -> dict[str, Any] | None:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def has_active_quarantine(self, document_id: str) -> bool:
        """Records quarantine (14.2, ADR 0116) - a separate call from `get()`
        since quarantine status is not a column on the document itself, but
        a joined `RecordsQuarantine` row. Deliberately the ungated `GET
        .../has-active-quarantine` (a document's own status isn't
        restricted-visibility content, only bulk listing is, per that
        endpoint's own docstring) - safe to call unconditionally during
        reindexing, same trust level as `get()` itself. `404` (unknown
        document, e.g. a race with `document.deleted`) is treated as `False`
        - `reindex_document()` already deletes the row for that case via its
        own `get()` call, this value is simply discarded."""
        response = await self._client.get(f"/documents/{document_id}/has-active-quarantine")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return response.json()["has_active_quarantine"]

    async def close(self) -> None:
        await self._client.aclose()
