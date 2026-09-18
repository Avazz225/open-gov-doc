import httpx


class ArchivalServiceClient:
    """HTTP client against the Archival Service (Post-Roadmap Phase 43
    Session 1, DMS-to-DMS XDOMEA handoff, ADR 0147/ADR 0159). Deliberately
    reuses the already-existing general-export/general-import HTTP
    endpoints unchanged (ADR 0127/0128) rather than adding a JSON-body
    import variant - `import_package` synthesizes the exact same multipart
    request a human uploading via the UI would send, just built from
    already-decoded bytes instead of a browser file picker (ADR 0147
    "Rationale" explicitly left this choice, not a JSON variant, as the
    lower-risk option for a future build session - this is that session).

    Same fixed system-identity header convention as `document_client.py`:
    `archival.write`/`archival.read` are both granted to "everyone" by
    default (Post-Roadmap Phase 19 Session 7, ADR 0072), so an asserted
    string identity with no real registered account is sufficient - no new
    permission-service role needed for this feature."""

    _SYSTEM_PRINCIPAL = "workflow-service"

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"X-DMS-Principal": self._SYSTEM_PRINCIPAL},
        )

    async def export_case(self, case_id: str, *, leser_name: str) -> bytes:
        response = await self._client.post(
            f"/xdomea/export/cases/{case_id}", params={"leser_name": leser_name}
        )
        response.raise_for_status()
        return response.content

    async def import_package(
        self, zip_bytes: bytes, *, folder_id: str, process_definition_id: int
    ) -> dict:
        """`created_by` on the resulting case/documents is `x_dms_principal`
        on the archival-service side (no separate form field exists) - set
        to `"federation-hub"` here, the same actor name already used
        throughout this module for everything else a federation inbound
        creates (the local BPMN instance, its completed tasks), overriding
        this client's own default system identity for just this call."""
        response = await self._client.post(
            "/xdomea/import",
            data={
                "folder_id": folder_id,
                "process_definition_id": str(process_definition_id),
            },
            files={"file": ("handoff.zip", zip_bytes, "application/zip")},
            headers={"X-DMS-Principal": "federation-hub"},
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
