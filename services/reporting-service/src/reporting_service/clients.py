from datetime import datetime

import httpx


class WorkflowClient:
    """Thin HTTP client against workflow-service (5.4a "open workflow
    tasks") - queried live instead of as its own read model, since no event
    marks a task as "ready" (only start/completion are published), so a
    read model could not be kept current without itself querying
    synchronously again."""

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
    """Thin HTTP client against audit-service - user activity (5.4a) directly
    uses the filter API built in P7-S2 (`actor`/`since`/`until`), no own
    read model needed, audit-service is already the authoritative
    source."""

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
        limit: int = 5000,
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


class StorageClient:
    """Thin HTTP client against storage-service - both for the storage usage
    report (`GET /storage/usage`, since P7-S2b) and for storing/retrieving
    generated report files (3.6 principle: the actual content never lives
    in the Reporting Service itself)."""

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
    """Thin HTTP client against notification-service - plain text email
    with a download link instead of an attachment (5.4a "schedulable
    (regular sending)"), see docs/services/reporting-service.md for the
    rationale."""

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
