from datetime import datetime

import httpx


class AuditServiceClient:
    """HTTP client for the one audit-service query the PDF export feature
    needs (post-roadmap phase 28, ADR 0107): a document's `document.exported`
    event history, queried instead of introducing a dedicated export-history
    storage of its own (see docs/services/document-service.md)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)

    async def list_export_history(self, document_id: str) -> list[dict]:
        """Returns `[]` on any error (fail-open, same convention as
        e.g. `office-addin`'s WorkflowPanel or `notification-service`'s
        recipient check for a webhook target) - an unreachable audit-service
        should not block an export, only make its history section empty."""
        try:
            response = await self._client.get(
                "/events",
                params={
                    "subject": document_id,
                    "event_type": "document.exported",
                    "limit": 1000,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        events = response.json()
        return [
            {
                "happened_at": _as_iso(event["occurred_at"]),
                "actor": event.get("actor"),
                "action": "exported",
            }
            for event in events
        ]

    async def close(self) -> None:
        await self._client.aclose()


def _as_iso(value: str) -> str:
    # Round-trips through `datetime` purely to normalize the format audit-
    # service returns into one `ExportHistoryEntryIn` (rendering-service)
    # reliably parses - avoids coupling the two services' exact datetime
    # serialization conventions.
    return datetime.fromisoformat(value).isoformat()
