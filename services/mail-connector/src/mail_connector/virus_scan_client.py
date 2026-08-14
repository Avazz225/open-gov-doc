from dataclasses import dataclass

import httpx


@dataclass
class ScanOutcome:
    scan_id: str
    status: str  # "clean" | "infected"


class VirusScanClient:
    """Invokes the mandatory virus scan (10.3, ADR 0010) for every inbound
    mail attachment - identical call to document-service's `POST /documents`,
    here for attachments that (unlike a regular upload) are not yet assigned
    to a document at the time of the scan (`document_id=None`, exactly the
    case the service itself provides for)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def scan(
        self, *, data: bytes, filename: str, content_type: str | None, created_by: str
    ) -> ScanOutcome:
        response = await self._client.post(
            "/scan",
            data={"created_by": created_by},
            files={"file": (filename, data, content_type or "application/octet-stream")},
        )
        response.raise_for_status()
        body = response.json()
        return ScanOutcome(scan_id=body["id"], status=body["status"])

    async def close(self) -> None:
        await self._client.aclose()
