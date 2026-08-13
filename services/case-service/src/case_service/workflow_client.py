import httpx


class ProcessDefinitionUnknownError(Exception):
    """`process_definition_id` existiert laut workflow-service nicht (404)."""


class WorkflowClient:
    """HTTP-Client gegen workflow-service (7.1, P6-S1) - case-service startet
    darueber die Prozessinstanz einer neuen Umlaufmappe und setzt deren
    `business_key` bewusst auf die Case-ID (siehe main.py:create_case),
    damit `consumer.py` den spaeteren Abschluss zuordnen kann."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def start_instance(
        self,
        process_definition_id: int,
        *,
        created_by: str,
        business_key: str,
        initial_data: dict,
        x_dms_principal: str,
    ) -> dict:
        """`x_dms_principal` (Post-Roadmap Phase 19 Session 9, ADR 0074):
        `POST .../instances` verlangt seither `workflow.write` - anders als
        `created_by` (ein ungeprüftes Body-Feld, s. u.) ist dies der bereits
        von `_require_case_permission` verifizierte Aufrufer aus dem
        eingehenden Request-Header."""
        response = await self._client.post(
            f"/process-definitions/{process_definition_id}/instances",
            json={
                "created_by": created_by,
                "business_key": business_key,
                "initial_data": initial_data,
            },
            headers={"X-DMS-Principal": x_dms_principal},
        )
        if response.status_code == 404:
            raise ProcessDefinitionUnknownError(
                f"process_definition_id {process_definition_id!r} unbekannt"
            )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
