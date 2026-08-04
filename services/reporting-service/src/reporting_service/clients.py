from datetime import datetime

import httpx


class WorkflowClient:
    """Duenner HTTP-Client gegen workflow-service (5.4a "offene Workflow-
    Aufgaben") - live abgefragt statt als eigenes Read-Modell, da kein
    Event einen Task als "bereit" markiert (nur Start/Abschluss werden
    publiziert), ein Read-Modell koennte also nicht aktuell gehalten
    werden ohne selbst wieder synchron nachzufragen."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def list_active_instances(self) -> list[dict]:
        response = await self._client.get("/instances", params={"status": "active"})
        response.raise_for_status()
        return response.json()

    async def list_tasks(self, instance_id: str) -> list[dict]:
        response = await self._client.get(f"/instances/{instance_id}/tasks")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class AuditClient:
    """Duenner HTTP-Client gegen audit-service - Nutzeraktivitaet (5.4a)
    nutzt direkt die in P7-S2 gebaute Filter-API (`actor`/`since`/`until`),
    kein eigenes Read-Modell noetig, audit-service ist bereits die
    autoritative Quelle."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def list_events(
        self,
        *,
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        params: dict[str, str | int] = {"limit": limit}
        if actor is not None:
            params["actor"] = actor
        if since is not None:
            params["since"] = since.isoformat()
        if until is not None:
            params["until"] = until.isoformat()
        response = await self._client.get("/events", params=params)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class StorageClient:
    """Duenner HTTP-Client gegen storage-service - sowohl fuer den
    Speicherverbrauch-Bericht (`GET /storage/usage`, seit P7-S2b) als auch
    fuer das Ablegen/Abrufen erzeugter Berichtsdateien (3.6-Prinzip: der
    eigentliche Inhalt liegt nie im Reporting Service selbst)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_usage(self) -> list[dict]:
        response = await self._client.get("/storage/usage")
        response.raise_for_status()
        return response.json()

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        response = await self._client.put(
            f"/objects/{key}", content=data, headers={"Content-Type": content_type}
        )
        response.raise_for_status()

    async def download(self, key: str) -> bytes:
        response = await self._client.get(f"/objects/{key}")
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()


class NotificationClient:
    """Duenner HTTP-Client gegen notification-service - reine Text-E-Mail
    mit Downloadlink statt Anhang (5.4a "planbar (regelmässiger Versand)"),
    siehe docs/services/reporting-service.md fuer die Begruendung."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def send_email(self, *, recipient: str, subject: str, body: str) -> None:
        response = await self._client.post(
            "/notifications",
            json={"channel": "email", "recipient": recipient, "subject": subject, "body": body},
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
