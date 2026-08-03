import hashlib
from datetime import datetime

import httpx


def compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectNotFoundError(Exception):
    """Der Storage Service kennt den angefragten Object-Key nicht (mehr) -
    z. B. weil die zugehörige Metadaten-Zeile verloren ging, während die
    Bytes selbst evtl. noch auf der Platte liegen (Inkonsistenz außerhalb
    der Kontrolle des Document Service)."""


class DeletionBlockedError(Exception):
    """Der Storage Service lehnt die Löschung wegen einer aktiven
    Governance-Mode-Sperre ab (5.1/5.2a, seit P7-S1)."""


class StorageClient:
    """Dünner HTTP-Client gegen die Storage-Service-API (3.6). Document
    Service hält nie selbst Dateiinhalte - reine Service-zu-Service-Kommunikation
    über die öffentliche API, kein Zugriff auf Storage-Service-Interna."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None,
        *,
        retain_until: datetime | None = None,
    ) -> None:
        """`retain_until` (5.1/5.2a, seit P7-S1) wird nur übergeben, wenn die
        Aufbewahrungsfrist bereits beim Schreiben bekannt ist (z. B. aus
        `ObjectType.default_retention_days`) - eine später über
        `PUT /documents/{id}/retention` gesetzte/geänderte Frist wirkt sich
        NICHT rückwirkend auf bereits gespeicherte Inhalte aus (kein
        Nachrüst-Endpunkt in storage-service, siehe docs/services/
        document-service.md "Offene Punkte")."""
        headers = {"Content-Type": content_type} if content_type else {}
        params = {"retain_until": retain_until.isoformat()} if retain_until else None
        response = await self._client.put(
            f"/objects/{key}", content=data, headers=headers, params=params
        )
        response.raise_for_status()

    async def download(self, key: str) -> bytes:
        response = await self._client.get(f"/objects/{key}")
        if response.status_code == 404:
            raise ObjectNotFoundError(key)
        response.raise_for_status()
        return response.content

    async def delete(
        self, key: str, *, bypass_governance: bool = False, x_dms_roles: str = ""
    ) -> None:
        response = await self._client.delete(
            f"/objects/{key}",
            params={"bypass_governance": bypass_governance},
            headers={"x-dms-roles": x_dms_roles} if x_dms_roles else None,
        )
        if response.status_code == 403:
            raise DeletionBlockedError(response.json().get("detail", "Löschung blockiert"))
        if response.status_code == 404:
            return  # bereits nicht (mehr) vorhanden - idempotent
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
