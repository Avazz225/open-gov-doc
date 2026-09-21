import httpx


class SignatureServiceClient:
    """HTTP-Client gegen den Signature Service (3.10, P6-S7): `POST
    .../tasks/{id}/complete` verlangt bei einer als `taskType=signature`
    markierten Task eine echte, existierende Signatur, die zum in den
    Task-Prozessdaten hinterlegten Dokument passt - siehe `main.py`."""

    # Fixed system-identity header (Phase 59 Session 3, ADR 0180) -
    # `GET /signatures/{id}` now requires `X-DMS-Principal` (401 without
    # it) and checks the caller's own `document.read` on the signature's
    # document (a baseline "everyone" grant for ordinary documents, ADR
    # 0149, so no dedicated `permission-service` grant is needed for this
    # literal identity). Previously sent no header at all - a real,
    # previously-unnoticed production bug (not just a test-fixture gap),
    # since this call was unauthenticated-and-thus-untested against the
    # gate until this session's own full-suite run surfaced it.
    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "workflow-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def get_signature(self, signature_id: str) -> dict | None:
        response = await self._client.get(f"/signatures/{signature_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
