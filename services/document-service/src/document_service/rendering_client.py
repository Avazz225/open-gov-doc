import json

import httpx


class RenderingUnavailableError(Exception):
    """rendering-service was unreachable, or returned an error response, for
    an export request (post-roadmap phase 28, ADR 0107)."""


class RenderingClient:
    """HTTP client for the parts of rendering-service's API used by the PDF
    export feature (post-roadmap phase 28, ADR 0107) - document-service
    fetches document content/export history itself and hands them to
    rendering-service, which owns all PDF conversion/merge/bookmark logic
    (see rendering_service/export_pdf.py)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=90.0)

    async def export_document(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        title: str,
        history_position: str,
        history: list[dict],
        x_dms_principal: str,
    ) -> bytes:
        files = {"file": (filename, data, content_type or "application/octet-stream")}
        form = {
            "title": title,
            "history_position": history_position,
            "history": json.dumps(history),
        }
        try:
            response = await self._client.post(
                "/render/export/document",
                data=form,
                files=files,
                headers={"X-DMS-Principal": x_dms_principal},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RenderingUnavailableError(str(exc)) from exc
        return response.content

    async def export_folder(
        self, *, entries: list[tuple[str, bytes]], x_dms_principal: str
    ) -> bytes:
        """`entries` are `(title, export_pdf)` pairs, each `export_pdf`
        already the output of `export_document` (Pass A applied) for that
        document. `data` as a dict-of-list rather than a list-of-tuples -
        the latter combined with a list-valued `files` makes httpx build a
        request whose body stream isn't recognized as async-native,
        raising "Attempted to send an sync request with an AsyncClient
        instance" at send time."""
        form = {"titles": [title for title, _ in entries]}
        files = [
            ("files", (f"{index}.pdf", export_pdf, "application/pdf"))
            for index, (_, export_pdf) in enumerate(entries)
        ]
        try:
            response = await self._client.post(
                "/render/export/folder",
                data=form,
                files=files,
                headers={"X-DMS-Principal": x_dms_principal},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RenderingUnavailableError(str(exc)) from exc
        return response.content

    async def close(self) -> None:
        await self._client.aclose()
