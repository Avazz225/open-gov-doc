import httpx


class ProcessDefinitionUnknownError(Exception):
    """`process_definition_id` does not exist according to workflow-service (404)."""


class WorkflowClient:
    """HTTP client against workflow-service (7.1, P6-S1) - case-service starts
    the process instance of a new circulation folder through it and deliberately
    sets its `business_key` to the case ID (see main.py:create_case),
    so that `consumer.py` can later match the completion to it."""

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
        `POST .../instances` has required `workflow.write` since then - unlike
        `created_by` (an unvalidated body field, see below), this is the caller
        already verified by `_require_case_permission`, taken from the
        incoming request header."""
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
