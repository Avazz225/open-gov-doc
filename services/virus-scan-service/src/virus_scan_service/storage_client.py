import httpx


class ObjectNotFoundError(Exception):
    """Der Storage Service kennt den angefragten Object-Key nicht (mehr)."""


class StorageClient:
    """Dünner HTTP-Client gegen die Storage-Service-API (3.6), identisch im
    Zuschnitt zum gleichnamigen Client des Document Service - auch der Virus-
    Scan Service hält nie selbst Dateiinhalte."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def upload(self, key: str, data: bytes, content_type: str | None) -> None:
        headers = {"Content-Type": content_type} if content_type else {}
        response = await self._client.put(f"/objects/{key}", content=data, headers=headers)
        response.raise_for_status()

    async def download(self, key: str) -> bytes:
        """Liest einen quarantänierten Objekt-Inhalt zurück (2.5, P15-S2) -
        z. B. um ihn bei einer Freigabe an den Document Service weiterzugeben."""
        response = await self._client.get(f"/objects/{key}")
        if response.status_code == 404:
            raise ObjectNotFoundError(key)
        response.raise_for_status()
        return response.content

    async def delete(self, key: str) -> None:
        """Entfernt den quarantänierten Objekt-Inhalt endgültig (2.5, P15-S2).
        Idempotent - ein bereits fehlendes Objekt ist kein Fehler, da eine
        Freigabe/Löschung nie zweimal dieselben Bytes benötigt."""
        response = await self._client.delete(f"/objects/{key}")
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
