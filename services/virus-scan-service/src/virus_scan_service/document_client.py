import json

import httpx


class DocumentServiceError(Exception):
    """Der Document Service hat die Freigabe eines Quarantäne-Falls abgelehnt
    (2.5, P15-S2) - z. B. unbekannte `folder_id`/`object_type_id` (400/422),
    fehlende Freigabe-Rolle auf Document-Service-Seite (401/403) oder
    ausgeschöpftes Lizenzlimit (403)."""

    def __init__(self, status_code: int, detail: object) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"document-service lehnte die Freigabe ab ({status_code}): {detail}")


class DocumentClient:
    """Dünner HTTP-Client gegen den internen Anlage-Pfad des Document Service
    (`POST /documents/from-quarantine-release`, 2.5/10.3) - ruft bewusst NICHT
    den regulären `POST /documents`-Endpunkt auf, da dieser jede Anlage
    ungefragt erneut scannen würde (siehe document-service main.py)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def create_from_quarantine_release(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        title: str,
        created_by: str,
        folder_id: str | None,
        object_type_id: int | None,
        attributes: dict,
        source_scan_id: str,
        x_dms_principal: str,
        x_dms_roles: str,
    ) -> dict:
        response = await self._client.post(
            "/documents/from-quarantine-release",
            data={
                "title": title,
                "created_by": created_by,
                "folder_id": folder_id,
                "object_type_id": object_type_id,
                "attributes": json.dumps(attributes),
                "source_scan_id": source_scan_id,
            },
            files={"file": (filename, data, content_type or "application/octet-stream")},
            headers={"X-DMS-Principal": x_dms_principal, "X-DMS-Roles": x_dms_roles},
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise DocumentServiceError(response.status_code, detail)
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
