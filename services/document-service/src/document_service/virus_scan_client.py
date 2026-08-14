import httpx


class ScanRejectedError(Exception):
    """The virus scan service has classified the file as infected (10.3)."""

    def __init__(self, threat_name: str | None) -> None:
        self.threat_name = threat_name
        super().__init__(f"Datei enthält Schadsoftware: {threat_name!r}")


class ScanUnavailableError(Exception):
    """The virus scan service was unreachable - fail-closed (ADR 0010):
    an upload is not let through just because the scan failed."""


class VirusScanClient:
    """HTTP client for the Virus Scan Service (10.3) - called
    synchronously *before* every write of content/metadata (ADR 0010), so
    that an upload is never released without a clean scan."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def scan(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        document_id: str | None,
        created_by: str,
    ) -> None:
        files = {"file": (filename, data, content_type or "application/octet-stream")}
        form: dict[str, str] = {"created_by": created_by}
        if document_id is not None:
            form["document_id"] = document_id

        try:
            response = await self._client.post("/scan", data=form, files=files)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ScanUnavailableError(str(exc)) from exc

        body = response.json()
        if body["status"] == "infected":
            raise ScanRejectedError(body.get("threat_name"))

    async def close(self) -> None:
        await self._client.aclose()
