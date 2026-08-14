import httpx

_CONFIG_ADMIN_PRINCIPAL_ID = "migration-service"


class WorkflowServiceClient:
    """Drives the actual transfer via a real BPMN instance in
    `workflow-service` (7.2: "itself runs as an auditable, resumable
    workflow via the workflow engine"). Idempotently uploads the bundled
    process definitions (`resources/*.bpmn`) on startup
    (uploading under the same name creates a new version, see
    docs/services/workflow-service.md "Versioning" - hence first check via
    `GET /process-definitions?name=` whether a version already
    exists, instead of creating another one on every restart)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=30.0,
            headers={"X-DMS-Principal": _CONFIG_ADMIN_PRINCIPAL_ID},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def ensure_process_definition(self, *, name: str, bpmn_xml: str) -> int:
        existing = await self._client.get("/process-definitions", params={"name": name})
        existing.raise_for_status()
        versions = existing.json()
        if versions:
            return versions[0]["id"]
        response = await self._client.post(
            "/process-definitions",
            data={"name": name},
            files={"bpmn_xml": ("process.bpmn", bpmn_xml, "application/xml")},
        )
        response.raise_for_status()
        return response.json()["id"]

    async def start_instance(
        self, definition_id: int, *, created_by: str, initial_data: dict, instance_id: str
    ) -> str:
        """`instance_id` is determined by the caller (P12-S2, see
        `ProcessInstanceCreate.instance_id` docstring in workflow-service) -
        important because even the very first automatic step (e.g.
        "lock") can fail: without an ID known in advance, if this call
        fails there would be no way to find the instance that was
        nevertheless created in workflow-service again for
        `POST /instances/{id}/retry`."""
        response = await self._client.post(
            f"/process-definitions/{definition_id}/instances",
            json={
                "created_by": created_by,
                "initial_data": initial_data,
                "instance_id": instance_id,
            },
        )
        response.raise_for_status()
        return response.json()["id"]
