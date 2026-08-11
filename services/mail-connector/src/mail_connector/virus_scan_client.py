from dataclasses import dataclass

import httpx


@dataclass
class ScanOutcome:
    scan_id: str
    status: str  # "clean" | "infected"


class VirusScanClient:
    """Ruft den verpflichtenden Virenscan (10.3, ADR 0010) für jeden
    Posteingang-Anhang auf - identischer Aufruf wie document-service's
    `POST /documents`, hier für Anhänge, die (anders als ein regulärer
    Upload) zum Zeitpunkt des Scans noch keinem Dokument zugeordnet sind
    (`document_id=None`, exakt der vom Service selbst vorgesehene Fall)."""

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
