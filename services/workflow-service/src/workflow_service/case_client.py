import logging

import httpx

logger = logging.getLogger(__name__)


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
        """P68-S1 (incidental fix): a transport-level failure (case-service
        genuinely unreachable - DNS/connection/timeout, not an HTTP error
        response) previously propagated as an unhandled exception, crashing
        the caller with `500` - unlike an ordinary `403`/`404`, which this
        function already treats as "does not resolve here" (see
        `_resolve_business_key_scope`'s own docstring in `main.py`). Now
        treated the same way: case-service being temporarily unreachable
        degrades to "unresolved", it does not crash the request."""
        try:
            response = await self._client.get(
                f"/cases/{case_id}", headers={"X-DMS-Principal": x_dms_principal}
            )
        except httpx.TransportError:
            logger.warning("case_service_unreachable_during_business_key_resolution")
            return None
        if response.status_code in (403, 404):
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
