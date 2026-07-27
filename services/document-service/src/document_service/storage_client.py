import hashlib

import httpx


def compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectNotFoundError(Exception):
    """Der Storage Service kennt den angefragten Object-Key nicht (mehr) -
    z. B. weil die zugehörige Metadaten-Zeile verloren ging, während die
    Bytes selbst evtl. noch auf der Platte liegen (Inkonsistenz außerhalb
    der Kontrolle des Document Service)."""


class StorageClient:
    """Dünner HTTP-Client gegen die Storage-Service-API (3.6). Document
    Service hält nie selbst Dateiinhalte - reine Service-zu-Service-Kommunikation
    über die öffentliche API, kein Zugriff auf Storage-Service-Interna."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def upload(self, key: str, data: bytes, content_type: str | None) -> None:
        headers = {"Content-Type": content_type} if content_type else {}
        response = await self._client.put(f"/objects/{key}", content=data, headers=headers)
        response.raise_for_status()

    async def download(self, key: str) -> bytes:
        response = await self._client.get(f"/objects/{key}")
        if response.status_code == 404:
            raise ObjectNotFoundError(key)
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()
