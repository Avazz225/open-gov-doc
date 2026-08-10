"""Dünne HTTP-Clients gegen die Services, deren Konfiguration exportiert/
importiert wird (7.3) - config-service hat kein eigenes Postgres-Schema,
jede Kategorie liest/schreibt direkt bei ihrem jeweiligen Owner-Service
(Domain-Owner-Prinzip, kein Duplizieren fremder Datenhaltung)."""

import httpx


class ObjectTypeServiceClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def list_object_types(self) -> list[dict]:
        response = await self._client.get("/object-types")
        response.raise_for_status()
        return response.json()

    async def get_layout(self, object_type_id: int, purpose: str) -> dict:
        response = await self._client.get(f"/object-types/{object_type_id}/layouts/{purpose}")
        response.raise_for_status()
        return response.json()

    async def create_object_type(self, payload: dict) -> dict:
        response = await self._client.post("/object-types", json=payload)
        response.raise_for_status()
        return response.json()

    async def update_object_type(self, object_type_id: int, payload: dict) -> dict:
        response = await self._client.put(f"/object-types/{object_type_id}", json=payload)
        response.raise_for_status()
        return response.json()

    async def put_layout(self, object_type_id: int, purpose: str, payload: dict) -> dict:
        response = await self._client.put(
            f"/object-types/{object_type_id}/layouts/{purpose}", json=payload
        )
        response.raise_for_status()
        return response.json()


class WorkflowServiceClient:
    _CONFIG_ADMIN_PRINCIPAL_ID = "config-service"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=30.0,
            headers={"X-DMS-Principal": self._CONFIG_ADMIN_PRINCIPAL_ID},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_latest_process_definitions(self) -> list[dict]:
        response = await self._client.get("/process-definitions")
        response.raise_for_status()
        return response.json()

    async def get_process_definition(self, definition_id: int) -> dict:
        response = await self._client.get(f"/process-definitions/{definition_id}")
        response.raise_for_status()
        return response.json()

    async def create_process_definition(self, *, name: str, bpmn_xml: str) -> dict:
        response = await self._client.post(
            "/process-definitions",
            data={"name": name},
            files={"bpmn_xml": ("process.bpmn", bpmn_xml, "application/xml")},
        )
        response.raise_for_status()
        return response.json()

    async def list_latest_dmn_definitions(self) -> list[dict]:
        response = await self._client.get("/dmn-definitions")
        response.raise_for_status()
        return response.json()

    async def get_dmn_definition(self, dmn_definition_id: int) -> dict:
        response = await self._client.get(f"/dmn-definitions/{dmn_definition_id}")
        response.raise_for_status()
        return response.json()

    async def create_dmn_definition(self, *, name: str, dmn_xml: str) -> dict:
        response = await self._client.post(
            "/dmn-definitions",
            data={"name": name},
            files={"dmn_xml": ("decision.dmn", dmn_xml, "application/xml")},
        )
        response.raise_for_status()
        return response.json()

    async def list_business_calendars(self) -> list[dict]:
        response = await self._client.get("/business-calendars")
        response.raise_for_status()
        return response.json()

    async def create_business_calendar(
        self, *, name: str, non_working_dates: list[str], is_default: bool
    ) -> dict:
        response = await self._client.post(
            "/business-calendars",
            json={"name": name, "non_working_dates": non_working_dates, "is_default": is_default},
        )
        response.raise_for_status()
        return response.json()

    async def update_business_calendar(
        self,
        business_calendar_id: int,
        *,
        name: str,
        non_working_dates: list[str],
        is_default: bool,
    ) -> dict:
        response = await self._client.put(
            f"/business-calendars/{business_calendar_id}",
            json={"name": name, "non_working_dates": non_working_dates, "is_default": is_default},
        )
        response.raise_for_status()
        return response.json()

    async def get_federation_config(self) -> dict:
        """7.4/P13-S3: Versionskompatibilitätsspanne - ungegatet auf der
        Zielseite, siehe `docs/services/workflow-service.md` "Federation"."""
        response = await self._client.get("/federation/config")
        response.raise_for_status()
        return response.json()

    async def put_federation_config(
        self, *, version: str, min_compatible_peer_version: str
    ) -> dict:
        response = await self._client.put(
            "/federation/config",
            json={"version": version, "min_compatible_peer_version": min_compatible_peer_version},
        )
        response.raise_for_status()
        return response.json()


class PermissionServiceClient:
    ROOT_RESOURCE_ID = "root"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def list_roles(self) -> list[dict]:
        response = await self._client.get("/roles")
        response.raise_for_status()
        return response.json()

    async def create_role(self, *, name: str, description: str, permissions: list[str]) -> dict:
        response = await self._client.post(
            "/roles", json={"name": name, "description": description, "permissions": permissions}
        )
        response.raise_for_status()
        return response.json()

    async def update_role(self, role_id: int, *, description: str, permissions: list[str]) -> dict:
        response = await self._client.put(
            f"/roles/{role_id}", json={"description": description, "permissions": permissions}
        )
        response.raise_for_status()
        return response.json()

    async def list_approval_configs(self) -> list[dict]:
        response = await self._client.get("/approval-config")
        response.raise_for_status()
        return response.json()

    async def put_approval_config(self, action_type: str, *, requires_approval: bool) -> dict:
        response = await self._client.put(
            f"/approval-config/{action_type}", json={"requires_approval": requires_approval}
        )
        response.raise_for_status()
        return response.json()


class MonitoringServiceClient:
    _CONFIG_ADMIN_PRINCIPAL_ID = "config-service"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=30.0,
            headers={"X-DMS-Principal": self._CONFIG_ADMIN_PRINCIPAL_ID},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_sensor_config(self) -> dict:
        response = await self._client.get("/sensor-config")
        response.raise_for_status()
        return response.json()

    async def put_global(self, enabled: bool) -> dict:
        # `PUT /sensor-config/global`/`.../{sensor_name}` verlangen
        # `admin.monitoring` (P11-S1) - `X-DMS-Principal` wird bereits im
        # Konstruktor gesetzt, siehe `main.py`s Bootstrap der
        # `domain-admin-monitoring`-Rolle für dasselbe Principal.
        response = await self._client.put("/sensor-config/global", json={"enabled": enabled})
        response.raise_for_status()
        return response.json()

    async def put_override(self, sensor_name: str, enabled: bool) -> dict:
        response = await self._client.put(
            f"/sensor-config/{sensor_name}", json={"enabled": enabled}
        )
        response.raise_for_status()
        return response.json()
