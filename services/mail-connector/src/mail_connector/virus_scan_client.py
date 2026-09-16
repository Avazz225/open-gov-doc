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
        # Post-Roadmap Phase 38 Session 2: `POST /scan` now requires
        # `X-DMS-Principal` + `virus_scan.write` (previously fully
        # ungated) - `virus_scan.write` was added to the "everyone" group
        # for exactly this kind of caller (no per-request human principal
        # for an inbound mail attachment), same fixed-identity pattern
        # already used by `document_service.virus_scan_client`. Missed for
        # this service in that session, found live in P38-S3's full test
        # suite run.
        response = await self._client.post(
            "/scan",
            data={"created_by": created_by},
            files={"file": (filename, data, content_type or "application/octet-stream")},
            headers={"X-DMS-Principal": "mail-connector"},
        )
        response.raise_for_status()
        body = response.json()
        return ScanOutcome(scan_id=body["id"], status=body["status"])

    async def close(self) -> None:
        await self._client.aclose()
