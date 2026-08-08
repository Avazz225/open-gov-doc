from dataclasses import dataclass

import httpx


@dataclass
class RawLicenseStatus:
    installed: bool
    valid: bool
    licensed_components: list[str] | None


class LicenseServiceClient:
    """Schlanker Client gegen `license-service` (Port 8023, P9-S1) - exaktes
    Vorbild `workflow_service.permission_client.PermissionServiceClient`."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_status(self) -> RawLicenseStatus:
        response = await self._client.get("/license/status")
        response.raise_for_status()
        data = response.json()
        return RawLicenseStatus(
            installed=data["installed"],
            valid=data["valid"],
            licensed_components=data.get("licensed_components"),
        )

    async def close(self) -> None:
        await self._client.aclose()
