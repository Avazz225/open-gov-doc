import hashlib
from datetime import datetime

import httpx


def compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectNotFoundError(Exception):
    """The Storage Service does not (no longer) know the requested
    object key - e.g. because the associated metadata row was lost while
    the bytes themselves may still be on disk (inconsistency outside the
    control of the Document Service)."""


class DeletionBlockedError(Exception):
    """The Storage Service refuses deletion due to an active
    governance-mode lock (5.1/5.2a, since P7-S1)."""


class StorageClient:
    """Thin HTTP client for the Storage Service API (3.6). Document
    Service never holds file content itself - pure service-to-service
    communication via the public API, no access to Storage Service
    internals."""

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
        """`retain_until` (5.1/5.2a, since P7-S1) is only passed when the
        retention period is already known at write time (e.g. from
        `ObjectType.default_retention_days`) - a retention period set/changed
        later via `PUT /documents/{id}/retention` does NOT apply
        retroactively to already stored content (no retrofit endpoint in
        storage-service, see docs/services/document-service.md "Open
        Points")."""
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
            return  # already (no longer) present - idempotent
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
