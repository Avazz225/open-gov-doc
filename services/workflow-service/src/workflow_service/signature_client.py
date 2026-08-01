import httpx


class SignatureServiceClient:
    """HTTP-Client gegen den Signature Service (3.10, P6-S7): `POST
    .../tasks/{id}/complete` verlangt bei einer als `taskType=signature`
    markierten Task eine echte, existierende Signatur, die zum in den
    Task-Prozessdaten hinterlegten Dokument passt - siehe `main.py`."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_signature(self, signature_id: str) -> dict | None:
        response = await self._client.get(f"/signatures/{signature_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
