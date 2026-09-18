import httpx

# Same fixed-technical-identity idea as migration-service's
# `_CONFIG_ADMIN_PRINCIPAL_ID` (no live end-user session exists when this
# fires - it's a background reaction to `document.created`/`document.
# version.created`), reusing the actor string `pipeline.py` already uses
# for OCR-created document versions (`OCR_SERVICE_ACTOR`) instead of
# inventing a second one.
OCR_SERVICE_PRINCIPAL_ID = "system:ocr-service"


class WorkflowServiceClient:
    """Drives OCR review as a real BPMN instance in workflow-service (3.9,
    Phase 45 Session 3 - deferred since P5-S3/P6-S1 pending workflow-service,
    which has existed since Phase 6). Idempotently uploads the bundled
    `resources/ocr_review.bpmn` on startup (uploading under the same name
    creates a new version, see docs/services/workflow-service.md
    "Versioning" - hence the `GET /process-definitions?name=` check first,
    same pattern as migration-service's own `WorkflowServiceClient.
    ensure_process_definition`)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=10.0,
            headers={"X-DMS-Principal": OCR_SERVICE_PRINCIPAL_ID},
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
        self, definition_id: int, *, business_key: str, initial_data: dict
    ) -> str:
        """`business_key` is set to the OCR result ID (`{document_id}:
        {version_number}`, see `models.OcrResult`'s own docstring) - unlike
        migration-service, there is no pre-existing local row that needs
        the instance ID persisted before the call (no automatic step runs
        before the first Manual Task here, so nothing could be "lost" on a
        transient failure the way an unreachable first `connector_call`
        step could for a transfer)."""
        response = await self._client.post(
            f"/process-definitions/{definition_id}/instances",
            json={
                "created_by": OCR_SERVICE_PRINCIPAL_ID,
                "business_key": business_key,
                "initial_data": initial_data,
            },
        )
        response.raise_for_status()
        return response.json()["id"]
