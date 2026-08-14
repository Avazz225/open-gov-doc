import httpx


class ApprovalClient:
    """HTTP client against the generic four-eyes approval mechanism in the
    permission service (4.3, P6-S4) - copy of
    `document_service.approval_client` (identical ~25-line pattern, no
    reason for a shared lib at this size). migration-service checks before
    starting a transfer whether `action_type="migration.transfer.start"`
    currently requires approval (7.2: "may itself be subject to the
    four-eyes principle")."""

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
