"""General XDOMEA export for inter-agency handoff (14.2, Post-Roadmap Phase
31 Session 13a, ADR 0126) - builds an `Abgabe.Abgabe.0401` package (ZIP:
`abgabe.xml` + `dokumente/<paketname>`) for an arbitrary `document-service`
Document or `case-service` Case, synchronously (a downloadable response,
same shape as document-service's `POST /documents/{id}/export`, P28-S1) -
NOT the disposal pipeline's async, multi-phase, encrypted transfer state
machine (`case_pipeline.py`/`pipeline.py`): this is a one-shot, on-demand
handoff package, not a legally significant records-disposal operation, so
none of that machinery's retry/verification/encryption phases apply here."""

import io
import zipfile

import httpx

from archival_service import xdomea
from archival_service.clients import CaseClient, DocumentClient

_ABGABE_MESSAGE_FILENAME = "abgabe.xml"


class ExportError(Exception):
    """The generated XDOMEA message failed schema validation (should be
    unreachable in practice - `xdomea.py`'s builders are exercised by their
    own unit tests - but caught explicitly rather than left to surface as an
    unhandled 500, same idiom as `pipeline.PipelineError`)."""


class ReferencedDocumentMissingError(Exception):
    """A case's `CaseDocumentReference` points at a document/version that no
    longer exists in document-service (data drift - e.g. the document was
    since deleted/purged while the reference itself was never cleaned up).
    Found live during this session's own verification against the real dev
    stack: a case's referenced document had genuinely been removed, and a
    blanket `httpx.HTTPStatusError` catch in `main.py` would otherwise
    mislabel this as "case unknown" - a materially different, misleading
    diagnosis. Deliberately a DISTINCT exception from a 404 on the case
    itself (`CaseClient.get_case`, checked by `main.py` before this function
    is even called)."""


async def build_document_export_package(
    document_id: str, *, document_client: DocumentClient, leser_name: str
) -> bytes:
    """Exports the CURRENT version only (same scope as document-service's
    own `POST /documents/{id}/export`, P28-S1) - not the full version
    history."""
    document = await document_client.get_document(document_id)
    version_number = document["current_version_number"]
    version = await document_client.get_version(document_id, version_number)
    content_type = version.get("content_type")
    filename = xdomea.package_filename(document_id, version_number, content_type)
    content = await document_client.download_version_content(document_id, version_number)

    entry = {
        "document_id": document_id,
        "version_number": version_number,
        "content_type": content_type,
        "original_filename": version.get("filename"),
        "package_filename": filename,
    }
    message_xml = xdomea.build_abgabe_message_for_document(entry, leser_name=leser_name)
    try:
        xdomea.validate_abgabe_message(message_xml)
    except xdomea.ValidationError as exc:
        raise ExportError(f"XDOMEA-Nachricht ungueltig: {exc}") from exc

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_ABGABE_MESSAGE_FILENAME, message_xml)
        archive.writestr(f"dokumente/{filename}", content)
    return buffer.getvalue()


async def build_case_export_package(
    case: dict,
    *,
    case_client: CaseClient,
    document_client: DocumentClient,
    leser_name: str,
) -> bytes:
    """Exports every currently-active document reference of the case (same
    `removed_at is None and snapshot_version_number is not None` filter
    `case_pipeline._build_package` already uses for the disposal path) -
    unlike disposal, the case does NOT need to be closed; any case may be
    exported for handoff. `case` is the ALREADY-FETCHED case dict (see
    `main.py`'s `export_case_xdomea` - fetching it there, not here, lets the
    caller cleanly distinguish "case unknown" (404 on `get_case` itself)
    from `ReferencedDocumentMissingError` (404 on one of the case's OWN
    document references) instead of a single blanket `httpx.HTTPStatusError`
    catch conflating both into a misleading "case unknown" - found live
    during this session's own verification, see that exception's
    docstring)."""
    case_id = case["id"]
    references = await case_client.list_document_references(case_id)
    active_references = [
        r
        for r in references
        if r["removed_at"] is None and r["snapshot_version_number"] is not None
    ]

    documents: list[dict] = []
    contents: dict[str, bytes] = {}
    for reference in active_references:
        document_id = reference["document_id"]
        version_number = reference["snapshot_version_number"]
        try:
            version = await document_client.get_version(document_id, version_number)
            content = await document_client.download_version_content(document_id, version_number)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ReferencedDocumentMissingError(
                    f"Vom Fall {case_id!r} referenziertes Dokument {document_id!r} "
                    f"(Version {version_number}) existiert nicht mehr"
                ) from exc
            raise
        content_type = version.get("content_type")
        filename = xdomea.package_filename(document_id, version_number, content_type)
        documents.append(
            {
                "document_id": document_id,
                "version_number": version_number,
                "content_type": content_type,
                "original_filename": version.get("filename"),
                "package_filename": filename,
            }
        )
        contents[filename] = content

    message_xml = xdomea.build_abgabe_message_for_case(case, documents, leser_name=leser_name)
    try:
        xdomea.validate_abgabe_message(message_xml)
    except xdomea.ValidationError as exc:
        raise ExportError(f"XDOMEA-Nachricht ungueltig: {exc}") from exc

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_ABGABE_MESSAGE_FILENAME, message_xml)
        for filename, data in contents.items():
            archive.writestr(f"dokumente/{filename}", data)
    return buffer.getvalue()
