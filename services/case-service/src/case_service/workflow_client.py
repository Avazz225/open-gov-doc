import httpx


class ProcessDefinitionUnknownError(Exception):
    """`process_definition_id` does not exist according to workflow-service (404)."""


class InstanceStartRejectedError(Exception):
    """workflow-service refused the start (422), e.g. a circulation route
    that is missing or names an unknown group (P93-S3). Carries its detail."""


class WorkflowUnavailableError(Exception):
    """The start's outcome is unknown (P95-S1c): a timeout, a connection
    error or a `5xx`. The instance may exist anyway - a timed-out start can
    still finish on workflow-service's side - so the caller must look it up
    (`find_instance`) before treating the start as failed."""


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
        try:
            response = await self._client.post(
                f"/process-definitions/{process_definition_id}/instances",
                json={
                    "business_key": business_key,
                    "initial_data": initial_data,
                },
                headers={"X-DMS-Principal": x_dms_principal},
            )
        except httpx.TransportError as exc:  # timeouts included
            raise WorkflowUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500:
            raise WorkflowUnavailableError(f"workflow-service antwortete {response.status_code}")
        if response.status_code == 404:
            raise ProcessDefinitionUnknownError(
                f"process_definition_id {process_definition_id!r} unbekannt"
            )
        if response.status_code == 422:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise InstanceStartRejectedError(
                detail if isinstance(detail, str) else "Prozessstart abgelehnt"
            )
        response.raise_for_status()
        return response.json()

    async def find_instance(self, business_key: str) -> dict | None:
        """The instance started for `business_key` (a case id), or `None`.
        Raises `WorkflowUnavailableError` when workflow-service can't say."""
        try:
            response = await self._client.get(
                "/instances", params={"business_key": business_key, "limit": 1}
            )
        except httpx.TransportError as exc:
            raise WorkflowUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500:
            raise WorkflowUnavailableError(f"workflow-service antwortete {response.status_code}")
        response.raise_for_status()
        instances = response.json()
        return instances[0] if instances else None

    async def close(self) -> None:
        await self._client.aclose()
