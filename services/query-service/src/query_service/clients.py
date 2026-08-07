from datetime import datetime

import httpx


class AuditClient:
    """Duenner HTTP-Client gegen audit-service - nutzt die in P7-S2 gebaute
    Filter-API (`actor`/`subject`/`event_type`/`since`/`until`). audit-service
    ist bereits die autoritative Quelle (5.4b-Praezedenzfall, siehe
    reporting-service), query-service haelt kein eigenes Read-Modell."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def list_events(
        self,
        *,
        actor: str | None = None,
        subject: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: dict[str, str | int] = {"limit": limit}
        if actor is not None:
            params["actor"] = actor
        if subject is not None:
            params["subject"] = subject
        if event_type is not None:
            params["event_type"] = event_type
        if since is not None:
            params["since"] = since.isoformat()
        if until is not None:
            params["until"] = until.isoformat()
        response = await self._client.get("/events", params=params)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class DocumentClient:
    """Duenner HTTP-Client gegen document-service - wird ausschliesslich zur
    Aufloesung `document_id` -> `folder_id` gebraucht (siehe filtering.py):
    Dokumente sind selbst keine Permission-Resources, nur Ordner sind
    `ResourceNode`s (gleiche Begruendung wie in search-service)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_document(self, document_id: str) -> dict | None:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class PermissionServiceClient:
    """HTTP-Client gegen den Permission Service - `POST /check/batch`
    vereinigt RBAC-Rechte UND Bereichssperren (4.7) bereits in einer Antwort
    (1:1 Wiederverwendung des search-service-Musters), sowie
    `GET /effective-permissions/{principal}/root` fuer das Domain-Admin-Gate
    der Konsole selbst (1:1 Muster aus workflow-service)."""

    ROOT_RESOURCE_ID = "root"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def check_batch(
        self,
        *,
        principal_id: str,
        permission: str,
        access_type: str = "read",
        resource_ids: list[str],
    ) -> dict[str, bool]:
        if not resource_ids:
            return {}
        response = await self._client.post(
            "/check/batch",
            json={
                "principal_id": principal_id,
                "permission": permission,
                "access_type": access_type,
                "resource_ids": resource_ids,
            },
        )
        response.raise_for_status()
        return response.json()["results"]

    async def close(self) -> None:
        await self._client.aclose()


class AuthServiceClient:
    """HTTP-Client gegen auth-service - `GET /superuser/status` ist der
    einzige Weg, "ist der aktuelle Aufrufer der aktivierte Superuser" zu
    pruefen (4.6, kein Header-Shortcut), 1:1 Muster aus permission-service."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_active_superuser(self) -> tuple[bool, str | None]:
        response = await self._client.get("/superuser/status")
        response.raise_for_status()
        body = response.json()
        return body["active"], body.get("principal_id")

    async def close(self) -> None:
        await self._client.aclose()
