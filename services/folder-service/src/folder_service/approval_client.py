import httpx


class ApprovalClient:
    """HTTP-Client gegen den generischen Vier-Augen-Approval-Mechanismus im
    Permission Service (4.3, P6-S4) - identisches Muster wie
    `document_service.approval_client.ApprovalClient` (P7-S1), hier für den
    Aktionstyp `folder.force_delete` (5.2a, seit P7-S1b)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def requires_approval(self, action_type: str) -> bool:
        response = await self._client.get(f"/approval-config/{action_type}")
        response.raise_for_status()
        return response.json()["requires_approval"]

    async def create_request(self, *, action_type: str, initiated_by: str, payload: dict) -> dict:
        response = await self._client.post(
            "/approval-requests",
            json={"action_type": action_type, "initiated_by": initiated_by, "payload": payload},
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
