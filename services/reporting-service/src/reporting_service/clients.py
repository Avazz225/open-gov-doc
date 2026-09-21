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
        principal_id: str,
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
        # Post-Roadmap Phase 38 Session 2: audit-service now requires
        # `X-DMS-Principal` + `audit.read` (previously fully ungated) -
        # forwards the ALREADY-authenticated calling principal whose own
        # `reporting.read`/`reporting.forensic_trace` gate already ran, one
        # level up in `main.py`, rather than asserting a separate service
        # identity.
        response = await self._client.get(
            "/events", params=params, headers={"X-DMS-Principal": principal_id}
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class StorageClient:
    """Thin HTTP client against storage-service - both for the storage usage
    report (`GET /storage/usage`, since P7-S2b) and for storing/retrieving
    generated report files (3.6 principle: the actual content never lives
    in the Reporting Service itself)."""

    # P66-S1: `storage-service`'s object-CRUD endpoints have required a
    # trusted `X-DMS-Principal` since ADR 0179 (P59-S2) - this client never
    # sent one, a real, currently-broken bug (every report-file store/
    # fetch call would 403) found incidentally while closing a related
    # gap on `storage-service`'s own aggregate/maintenance endpoints.
    _PRINCIPAL_HEADERS = {"X-DMS-Principal": "reporting-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_usage(self) -> list[dict]:
        response = await self._client.get("/storage/usage", headers=self._PRINCIPAL_HEADERS)
        response.raise_for_status()
        return response.json()

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        response = await self._client.put(
            f"/objects/{key}",
            content=data,
            headers={"Content-Type": content_type, **self._PRINCIPAL_HEADERS},
        )
        response.raise_for_status()

    async def download(self, key: str) -> bytes:
        response = await self._client.get(f"/objects/{key}", headers=self._PRINCIPAL_HEADERS)
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()


class LicenseServiceClient:
    """Thin HTTP client against license-service - license utilization
    (5.4a, Phase 45 Session 1) reads the single global `GET /license/
    status` snapshot, which already computes all three dimensions
    (`documents`/`storage_gb`/`users`) live on every call - no own read
    model needed, same "already the authoritative live source" reasoning
    as `AuditClient` above. Deliberately ungated, no `X-DMS-Principal`
    forwarded: this endpoint is ungated on license-service's own side too
    (queried by `registry-service`/the admin UI the same way, see
    `docs/services/license-service.md`)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_status(self) -> dict:
        response = await self._client.get("/license/status")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class AuthServiceClient:
    """HTTP client against auth-service - `GET /superuser/status` is the
    only way to check "is the current caller the activated superuser" (4.6,
    no header shortcut), 1:1 pattern from `permission-service`/
    `query-service`. Reporting-service had no superuser-bypass concept
    before Post-Roadmap Phase 36 Session 3 - added for parity with
    `query-service`'s own row-level RBAC filtering, since a forensic-trace
    investigation during an active break-glass incident should see
    everything, the same exception the concept (6.1) already carves out for
    structured queries."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_active_superuser(self) -> tuple[bool, str | None]:
        response = await self._client.get("/superuser/status")
        response.raise_for_status()
        body = response.json()
        return body["active"], body.get("principal_id")

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
        # Post-Roadmap Phase 38 Session 2: `POST /notifications` now
        # requires `X-DMS-Principal` + `notification.write` (previously
        # fully ungated) - asserts the same fixed scheduler identity
        # `main.py` already uses for its own `audit-service` calls in this
        # poll tick, granted `notification.write` via a dedicated role
        # (deliberately NOT part of "everyone", see notification-service's
        # `_require_notification_permission`).
        response = await self._client.post(
            "/notifications",
            json={"channel": "email", "recipient": recipient, "subject": subject, "body": body},
            headers={"X-DMS-Principal": "reporting-service-scheduler"},
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
