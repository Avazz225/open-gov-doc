import httpx
from dms_common import SERVICE_PRINCIPALS, acting_headers

_SERVICE_PRINCIPAL = "folder-service"


def _on_behalf_headers(initiated_by: str) -> dict[str, str]:
    """This service as principal, acting for ``initiated_by`` - unless the
    initiator is a service or a ``system:*`` job itself, which then simply is
    the service (ADR 0240)."""
    if not initiated_by or initiated_by in SERVICE_PRINCIPALS or initiated_by.startswith("system:"):
        return acting_headers(_SERVICE_PRINCIPAL)
    return acting_headers(_SERVICE_PRINCIPAL, on_behalf_of=initiated_by)


class ApprovalClient:
    """HTTP client against the generic four-eyes principle approval mechanism
    in the Permission Service (4.3, P6-S4) - identical pattern to
    `document_service.approval_client.ApprovalClient` (P7-S1), here for the
    action type `folder.force_delete` (5.2a, since P7-S1b)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def requires_approval(self, action_type: str) -> bool:
        response = await self._client.get(f"/approval-config/{action_type}")
        response.raise_for_status()
        return response.json()["requires_approval"]

    async def create_request(self, *, action_type: str, initiated_by: str, payload: dict) -> dict:
        response = await self._client.post(
            "/approval-requests",
            json={"action_type": action_type, "payload": payload},
            # P85-S2 (ADR 0240): the initiator is the acting identity - this
            # service, acting on behalf of the user who triggered the action.
            headers=_on_behalf_headers(initiated_by),
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
