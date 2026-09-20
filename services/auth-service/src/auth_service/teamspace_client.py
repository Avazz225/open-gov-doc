import logging

import httpx

logger = logging.getLogger(__name__)


class TeamspaceClient:
    """P55-S2: `DELETE /users/{id}` cross-service cleanup - removes a
    deleted user's teamspace memberships too. Fail-soft (logs and returns,
    same convention as `LicenseLimitClient`'s unreachable handling): a
    deleted user's Keycloak account and `permission-service` role
    assignments (the security-relevant parts) are already gone by the time
    this call runs, so a `teamspace-service` outage shouldn't turn a
    completed user deletion into a hard failure - the same eventual-
    cleanup tolerance ADR 0175's own consumer already accepts for a
    genuinely unreachable dependency, just synchronous here instead of
    event-driven."""

    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0, transport=transport)

    async def delete_memberships(self, principal_id: str) -> None:
        try:
            response = await self._client.delete(
                f"/principals/{principal_id}/teamspace-memberships",
                headers={"X-DMS-Principal": "auth-service"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "teamspace_membership_cleanup_failed: principal_id=%s", principal_id, exc_info=True
            )

    async def close(self) -> None:
        await self._client.aclose()
