import httpx

# RBAC (Post-Roadmap Phase 19 Session 5, ADR 0070) - since this session
# case-service checks `case.read`/`case.write` against permission-service and
# requires an `X-DMS-Principal` header for that. `mail-connector` acts here as
# a pure machine caller (triggered by incoming mail, no human principal
# present) - synthetic identifier following the same "system:<Service>"
# pattern used elsewhere in the project (e.g. `actor="system:archival-service"`
# on published events). The "everyone" group (ADR 0067) grants
# `case.read`/`case.write` by default to every authenticated principal, no
# dedicated technical account needed.
_SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "system:mail-connector"}


class CaseClient:
    """Thin HTTP client against the case-service API - Vorgangsnummer
    (case number) assignment lookup (P15-S3) and adding the documents newly
    created during a case assignment as a reference to the matched
    circulation folder (2.3, already-existing endpoint, no extension
    needed)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def lookup_by_vorgangsnummer(self, value: str) -> list[dict]:
        response = await self._client.get(
            "/cases/by-vorgangsnummer",
            params={"value": value},
            headers=_SYSTEM_PRINCIPAL_HEADERS,
        )
        response.raise_for_status()
        return response.json()

    async def get(self, case_id: str) -> dict | None:
        response = await self._client.get(f"/cases/{case_id}", headers=_SYSTEM_PRINCIPAL_HEADERS)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def get_number_config(self) -> dict:
        """Post-Roadmap Phase 19 Session 11 - returns `CaseNumberConfig.
        format` (2.5, a global `{placeholder}` format string), from which
        `matching.py` derives the actually configured candidate pattern,
        instead of a hard-coded generic pattern."""
        response = await self._client.get("/case-number-config", headers=_SYSTEM_PRINCIPAL_HEADERS)
        response.raise_for_status()
        return response.json()

    async def add_document_reference(
        self, case_id: str, *, document_id: str, added_by: str
    ) -> None:
        response = await self._client.post(
            f"/cases/{case_id}/documents",
            json={"document_id": document_id, "added_by": added_by},
            headers=_SYSTEM_PRINCIPAL_HEADERS,
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
