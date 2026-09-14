from typing import Any

import httpx


class DocumentServiceClient:
    """HTTP client against the Document Service (3.1). Document events are
    deliberately thin (see consumer.py) - the full record is reloaded here
    on every event, instead of relying on event payload fields."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

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
