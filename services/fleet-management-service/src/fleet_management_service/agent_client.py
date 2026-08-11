"""HTTP-Client gegen den Gateway einer verwalteten Installation (3a) - immer
über ``{gateway_base_url}/api/{service}/...``, nie eine interne Container-
Adresse, damit eine eingehende Zustellung dieselbe Wartungsmodus-/Rate-Limit-
Logik respektiert wie jeder andere Request (gleiches Prinzip wie
`workflow_service`s ``callback_base_url`` zum Federation Hub, ADR 0028).

Berührt ausschließlich `registry-service` (Identität), `license-service`
(Lizenzstatus/-installation) und `config-service` (Konfigurationsimport) -
nie `document-service`/`folder-service`. Das ist wörtliche Konzeptvorgabe
(3a: "Dieser Service hat aber keinen Zugriff auf Dokumenteninhalte einzelner
Installationen, nur auf deren Lizenz-/Statusmeldungen")."""

import httpx


class AgentError(Exception):
    """Netzwerkfehler oder Nicht-2xx-Antwort einer verwalteten Installation."""

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
        """``GET /api/registry-service/installation`` - ungegatet auf der
        Zielseite (P13-S1), der Fleet-Agent-Schlüssel wird hier zwar
        mitgeschickt, aber von `registry-service` nicht ausgewertet."""
        response = await self._client.get("/api/registry-service/installation")
        response.raise_for_status()
        return response.json()

    async def get_license_status(self) -> dict:
        """``GET /api/license-service/license/status`` - ebenfalls ungegatet
        auf der Zielseite (P9-S2/P13-S1)."""
        response = await self._client.get("/api/license-service/license/status")
        response.raise_for_status()
        return response.json()

    async def upload_license(self, license_token: str) -> dict:
        """``POST /api/license-service/license`` - gegated hinter dem
        installationsseitigen `DMS_FLEET_AGENT_API_KEY` (P13-S2)."""
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
        """``POST /api/config-service/config/fleet-import`` - gegated hinter
        demselben `DMS_FLEET_AGENT_API_KEY` (P13-S2), reiner Durchreicher des
        vom Betreiber mitgegebenen Konfigurationsdokuments (7.3). Eigener,
        dedizierter Pfad seit P17-S1 (vorher `config/import`, geteilt mit dem
        RBAC-Zugriffsweg - siehe `gateway_service.settings.public_routes` und
        `config_service.main.fleet_import_config` für die Begründung der
        Trennung)."""
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
