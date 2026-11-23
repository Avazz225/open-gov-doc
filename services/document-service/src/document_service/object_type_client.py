import time

import httpx


class MissingKennzeichenAttributeError(Exception):
    """An attribute placeholder referenced in `kennzeichen_format`
    (P17-S2, 14.2) did not receive a value when creating - object-type-service
    returns `422` for this, see its `repository.MissingKennzeichenAttributeError`."""


class ObjectTypeClient:
    """HTTP client for the Object-Type Service (2.2/4.5) - Document Service
    calls its `/validate` endpoint instead of embedding the
    constraint-engine library itself (no import of foreign service internals)."""

    # While the filing structure is "off" (the default, ADR 0255), the check
    # is skipped for this long, so an installation without a structure pays
    # no extra call per upload. Switching the mode on takes effect within it.
    STRUCTURE_OFF_CACHE_SECONDS = 15.0

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._structure_off_until = 0.0

    async def validate(
        self,
        object_type_id: int,
        *,
        name: str,
        attributes: dict,
        parent_object_type_id: int | None = None,
        parent_is_root: bool = False,
    ) -> list[str]:
        response = await self._client.post(
            f"/object-types/{object_type_id}/validate",
            json={
                "name": name,
                "attributes": attributes,
                "parent_object_type_id": parent_object_type_id,
                "parent_is_root": parent_is_root,
            },
        )
        response.raise_for_status()
        return response.json()["errors"]

    async def get(self, object_type_id: int) -> dict | None:
        """Retention (5.2, since P7-S1): reads `default_retention_days`/
        `deletion_reason_required_override` for the object type - `None` for
        an unknown `object_type_id` instead of an error, since the caller has
        already checked its existence via `validate()` at this point."""
        response = await self._client.get(f"/object-types/{object_type_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def next_kennzeichen(
        self, object_type_id: int, attributes: dict | None = None
    ) -> str | None:
        """Reference number generator (2.2, P5e-S2, since P17-S2 with
        attribute values for attribute-based placeholders like
        `{Federführung}`, 14.2) - `None` means that no generator is
        configured for this document class (404), not that the object type
        itself is unknown (that was already checked via `validate()` before
        this call is made)."""
        response = await self._client.post(
            f"/object-types/{object_type_id}/next-kennzeichen",
            json={"attributes": attributes or {}},
        )
        if response.status_code == 404:
            return None
        if response.status_code == 422:
            raise MissingKennzeichenAttributeError(response.json().get("detail", response.text))
        response.raise_for_status()
        return response.json()["kennzeichen"]

    async def check_structure(
        self,
        *,
        parent_object_type_id: int | None,
        parent_is_root: bool,
        child_object_type_id: int | None,
    ) -> dict:
        """Parent-side filing-structure check for a document (P91-S2a, ADR
        0255): `{"enforcement", "errors", "default_object_type_id"}`."""
        if time.monotonic() < self._structure_off_until:
            return {"enforcement": "off", "errors": [], "default_object_type_id": None}
        response = await self._client.post(
            "/structure/check",
            json={
                "parent_object_type_id": parent_object_type_id,
                "parent_is_root": parent_is_root,
                "child_kind": "document",
                "child_object_type_id": child_object_type_id,
            },
        )
        response.raise_for_status()
        result = response.json()
        if result["enforcement"] == "off":
            self._structure_off_until = time.monotonic() + self.STRUCTURE_OFF_CACHE_SECONDS
        return result

    async def close(self) -> None:
        await self._client.aclose()
