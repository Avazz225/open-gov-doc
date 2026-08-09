import httpx

_CONFIG_ADMIN_PRINCIPAL_ID = "migration-service"


class WorkflowServiceClient:
    """Treibt den eigentlichen Transfer über eine echte BPMN-Instanz in
    `workflow-service` an (7.2: "läuft selbst als auditierbarer, resumable
    Workflow über die Workflow Engine"). Lädt die mitgelieferten
    Prozessdefinitionen (`resources/*.bpmn`) beim Start idempotent hoch
    (Upload unter demselben Namen legt eine neue Version an, siehe
    docs/services/workflow-service.md "Versionierung" - daher erst per
    `GET /process-definitions?name=` prüfen, ob bereits eine Version
    existiert, statt bei jedem Neustart eine weitere anzulegen)."""

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
        """`instance_id` wird vom Aufrufer bestimmt (P12-S2, siehe
        `ProcessInstanceCreate.instance_id`-Docstring in workflow-service) -
        wichtig, weil bereits der allererste automatische Schritt (z. B.
        "Sperren") fehlschlagen kann: ohne eine im Voraus bekannte ID gäbe es
        bei einem Fehlschlag dieses Aufrufs keine Möglichkeit, die dennoch in
        workflow-service angelegte Instanz für `POST /instances/{id}/retry`
        wiederzufinden."""
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
