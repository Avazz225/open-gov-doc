import httpx


class CaseServiceClient:
    """HTTP client against the Case Service (P32-S2, ADR 0048's own
    anticipated "additional resolution step") - resolves a
    `ProcessInstance.business_key` to the underlying case's
    `object_type_id`, so a delegation's `scope_object_type_ids` can
    actually be evaluated at task completion. Every real circulation-folder
    process sets `business_key=case_id` (see `case_service.workflow_client`),
    so this is the primary, actually-exercised resolution path - see
    `main.py`'s `_resolve_business_key_scope`."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_case(self, case_id: str, *, x_dms_principal: str) -> dict | None:
        response = await self._client.get(
            f"/cases/{case_id}", headers={"X-DMS-Principal": x_dms_principal}
        )
        if response.status_code in (403, 404):
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
