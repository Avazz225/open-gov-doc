"""HTTP client against the gateway of a managed installation (3a) - always
via ``{gateway_base_url}/api/{service}/...``, never an internal container
address, so that an incoming delivery respects the same maintenance-mode/
rate-limit logic as any other request (same principle as `workflow_service`'s
``callback_base_url`` to the Federation Hub, ADR 0028).

Touches only `registry-service` (identity), `license-service` (license
status/installation) and `config-service` (configuration import) - never
`document-service`/`folder-service`. This is a literal concept requirement
(3a: "However, this service has no access to document contents of individual
installations, only to their license/status reports")."""

import httpx


class AgentError(Exception):
    """Network error or non-2xx response from a managed installation."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FleetAgentClient:
    def __init__(
        self,
        *,
        gateway_base_url: str,
        fleet_agent_api_key: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=gateway_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {fleet_agent_api_key}"},
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_installation_identity(self) -> dict:
        """``GET /api/registry-service/installation`` - not gated on the
        target side (P13-S1); the fleet-agent key is sent along here, but is
        not evaluated by `registry-service`."""
        response = await self._client.get("/api/registry-service/installation")
        response.raise_for_status()
        return response.json()

    async def get_license_status(self) -> dict:
        """``GET /api/license-service/license/status`` - likewise not gated
        on the target side (P9-S2/P13-S1)."""
        response = await self._client.get("/api/license-service/license/status")
        response.raise_for_status()
        return response.json()

    async def upload_license(self, license_token: str) -> dict:
        """``POST /api/license-service/license`` - gated behind the
        installation-side `DMS_FLEET_AGENT_API_KEY` (P13-S2)."""
        response = await self._client.post(
            "/api/license-service/license", json={"license_token": license_token}
        )
        if response.is_error:
            raise AgentError(
                f"Lizenz-Upload fehlgeschlagen: {response.text}",
                status_code=response.status_code,
            )
        return response.json()

    async def provision_config(
        self, config_document: dict, *, categories: list[str] | None
    ) -> dict:
        """``POST /api/config-service/config/fleet-import`` - gated behind
        the same `DMS_FLEET_AGENT_API_KEY` (P13-S2), a pure pass-through of
        the configuration document supplied by the operator (7.3). Its own,
        dedicated path since P17-S1 (previously `config/import`, shared with
        the RBAC access path - see `gateway_service.settings.public_routes`
        and `config_service.main.fleet_import_config` for the reasoning
        behind the separation)."""
        params = {"categories": categories} if categories else None
        response = await self._client.post(
            "/api/config-service/config/fleet-import", json=config_document, params=params
        )
        if response.is_error:
            raise AgentError(
                f"Provisionierung fehlgeschlagen: {response.text}",
                status_code=response.status_code,
            )
        return response.json()
