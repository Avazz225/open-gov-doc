from typing import Any

import httpx


class DocumentServiceClient:
    """HTTP-Client gegen den Document Service (3.1). Dokument-Events sind
    bewusst dünn (siehe consumer.py) - der volle Datensatz wird bei jedem
    Event hier nachgeladen, statt sich auf Event-Payload-Felder zu verlassen."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get(self, document_id: str) -> dict[str, Any] | None:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
