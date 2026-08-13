import httpx


class OcrServiceClient:
    """Dünner HTTP-Client gegen den OCR Service (3.1) - für den Nachzieheffekt
    aus P5-S3 (2.4/3.9: der OCR-Volltext speist eine `substitute_text`-
    Ersatzdarstellung für Dokumente, die P5-S2 mangels OCR nicht bedienen
    konnte). `ocr.completed`-Events tragen bewusst nur Statusfelder, nicht den
    potenziell großen Volltext selbst (siehe consumer.py) - dieser Client holt
    ihn bei Bedarf per HTTP nach."""

    # RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - `GET /ocr-results/
    # {id}` verlangt seither `ocr.read`; dieser Aufruf läuft aus einem
    # NATS-Consumer heraus, kein menschlicher Principal verfügbar - gleiches
    # `system:<Service>`-Muster wie `archival-service`s `CaseClient`.
    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "system:rendering-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def get_full_text(self, document_id: str, version_number: int) -> str | None:
        response = await self._client.get(f"/ocr-results/{document_id}:{version_number}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["full_text"]

    async def close(self) -> None:
        await self._client.aclose()
