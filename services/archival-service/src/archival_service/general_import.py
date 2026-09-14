"""General XDOMEA/XJustiz import for inter-agency handoff (14.2, XDOMEA:
Post-Roadmap Phase 31 Session 13b, ADR 0128; XJustiz: Post-Roadmap Phase 34
Session 1, ADR 0139) - the mirror of `general_export.py`: accepts an
`Abgabe.Abgabe.0401` or `nachricht.gds.uebermittlungSchriftgutobjekte.0005005`
package (same ZIP shape `general_export.py` produces for each), validates +
parses it, and creates the corresponding `document-service` Document(s) and,
depending on the caller's choice, either attaches them to an EXISTING
`case-service` Case or creates a brand-new one (started via a caller-supplied
`process_definition_id` - there is no XDOMEA/XJustiz-derivable value for
this, see ADR 0128 "Decision", reused unchanged for XJustiz). Synchronous,
same execution-model reasoning as `general_export.py` - a one-shot, on-demand
action, not the disposal pipeline's async multi-phase state machine."""

import io
import zipfile
from dataclasses import dataclass, field

from archival_service import xdomea, xjustiz
from archival_service.clients import CaseClient, DocumentClient

_ABGABE_MESSAGE_FILENAME = "abgabe.xml"
_XJUSTIZ_MESSAGE_FILENAME = "xjustiz_nachricht.xml"


class InvalidPackageError(Exception):
    """The uploaded file isn't a valid XDOMEA/XJustiz import package - not a
    real ZIP, missing the expected message file (`abgabe.xml`/
    `xjustiz_nachricht.xml`), fails schema validation, or a document the XML
    references has no matching `dokumente/<Dateiname>` entry in the ZIP.
    Shared between both import paths (format-agnostic by name already)."""


class CaseTargetConflictError(Exception):
    """Both `case_id` and `process_definition_id` were supplied - mutually
    exclusive, see `import_abgabe_package`'s/
    `import_uebermittlung_schriftgutobjekte_package`'s docstrings."""


class CaseTargetRequiredError(Exception):
    """The package contains a `Vorgang` (XDOMEA) or `akte` (XJustiz) but the
    caller supplied neither `case_id` nor `process_definition_id` - importing
    its documents with no case target at all would silently drop that
    context, the same "don't silently discard the case relationship"
    principle `mail_connector.assign_manually`'s mandatory `folder_id`
    already applies."""


class ProcessDefinitionWithoutVorgangError(Exception):
    """`process_definition_id` was supplied but the package contains no
    `Vorgang` at all - there is no Betreff to name the new case after."""


class ProcessDefinitionWithoutAkteError(Exception):
    """XJustiz counterpart to `ProcessDefinitionWithoutVorgangError` above -
    `process_definition_id` was supplied but the package contains no `akte`
    at all, so there is no `anzeigename` to name the new case after."""


@dataclass
class ImportResult:
    case_id: str | None
    case_created: bool
    vorgang_betreff: str | None
    document_ids: list[str] = field(default_factory=list)


@dataclass
class XJustizImportResult:
    case_id: str | None
    case_created: bool
    akte_anzeigename: str | None
    document_ids: list[str] = field(default_factory=list)


async def import_abgabe_package(
    zip_bytes: bytes,
    *,
    folder_id: str,
    case_id: str | None,
    process_definition_id: int | None,
    created_by: str,
    case_client: CaseClient,
    document_client: DocumentClient,
) -> ImportResult:
    if case_id is not None and process_definition_id is not None:
        raise CaseTargetConflictError(
            "case_id und process_definition_id schliessen sich gegenseitig aus - "
            "entweder an einen bestehenden Fall anhaengen ODER einen neuen Fall anlegen"
        )

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            message_xml = archive.read(_ABGABE_MESSAGE_FILENAME)
            contents = {
                name: archive.read(name)
                for name in archive.namelist()
                if name.startswith("dokumente/")
            }
    except (zipfile.BadZipFile, KeyError) as exc:
        raise InvalidPackageError(
            f"Kein gueltiges XDOMEA-Abgabe-Paket (ZIP fehlerhaft oder 'abgabe.xml' fehlt): {exc}"
        ) from exc

    try:
        xdomea.validate_abgabe_message(message_xml)
    except xdomea.ValidationError as exc:
        raise InvalidPackageError(f"XDOMEA-Nachricht ungueltig: {exc}") from exc

    try:
        parsed = xdomea.parse_abgabe_message(message_xml)
    except xdomea.ParseError as exc:
        raise InvalidPackageError(str(exc)) from exc

    if parsed.vorgang_betreff is None and process_definition_id is not None:
        raise ProcessDefinitionWithoutVorgangError(
            "process_definition_id angegeben, aber das Paket enthaelt keinen Vorgang, "
            "dessen Betreff als Name fuer den neuen Fall dienen koennte"
        )
    if parsed.vorgang_betreff is not None and case_id is None and process_definition_id is None:
        raise CaseTargetRequiredError(
            "Das Paket enthaelt einen Vorgang - bitte case_id (an bestehenden Fall anhaengen) "
            "oder process_definition_id (neuen Fall anlegen) angeben"
        )

    target_case_id = case_id
    case_created = False
    if process_definition_id is not None:
        attributes = (
            {"xdomea_herkunft_uuid": parsed.vorgang_xdomea_uuid}
            if parsed.vorgang_xdomea_uuid
            else {}
        )
        case = await case_client.create_case(
            name=parsed.vorgang_betreff,
            process_definition_id=process_definition_id,
            created_by=created_by,
            attributes=attributes,
        )
        target_case_id = case["id"]
        case_created = True

    document_ids: list[str] = []
    for doc in parsed.documents:
        key = f"dokumente/{doc.dateiname}"
        if key not in contents:
            raise InvalidPackageError(
                f"Im Paket referenzierte Datei {doc.dateiname!r} fehlt im ZIP-Archiv"
            )
        created = await document_client.create_document(
            data=contents[key],
            filename=doc.original_filename,
            content_type=doc.content_type,
            title=doc.original_filename,
            created_by=created_by,
            folder_id=folder_id,
        )
        document_ids.append(created["id"])
        if target_case_id is not None:
            await case_client.add_document_reference(
                target_case_id, document_id=created["id"], added_by=created_by
            )

    return ImportResult(
        case_id=target_case_id,
        case_created=case_created,
        vorgang_betreff=parsed.vorgang_betreff,
        document_ids=document_ids,
    )


async def import_uebermittlung_schriftgutobjekte_package(
    zip_bytes: bytes,
    *,
    folder_id: str,
    case_id: str | None,
    process_definition_id: int | None,
    created_by: str,
    case_client: CaseClient,
    document_client: DocumentClient,
) -> XJustizImportResult:
    """XJustiz counterpart to `import_abgabe_package` above (14.2,
    Post-Roadmap Phase 34 Session 1, ADR 0139) - accepts a `nachricht.gds.
    uebermittlungSchriftgutobjekte.0005005` package (same ZIP shape
    `general_export.build_document_export_package_xjustiz`/
    `build_case_export_package_xjustiz` produce), creates the referenced
    document(s) in `folder_id`. If the package contains an `akte`, exactly
    one of `case_id` (attach to an EXISTING case) or `process_definition_id`
    (start a brand-new case via this process definition, named after the
    Akte's `anzeigename`) must be given - same target-option shape as the
    XDOMEA import above, same ADR 0128 reasoning for why case creation
    requires a caller-supplied `process_definition_id` rather than being
    derived from the XJustiz data (there is no such value in it either)."""
    if case_id is not None and process_definition_id is not None:
        raise CaseTargetConflictError(
            "case_id und process_definition_id schliessen sich gegenseitig aus - "
            "entweder an einen bestehenden Fall anhaengen ODER einen neuen Fall anlegen"
        )

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            message_xml = archive.read(_XJUSTIZ_MESSAGE_FILENAME)
            contents = {
                name: archive.read(name)
                for name in archive.namelist()
                if name.startswith("dokumente/")
            }
    except (zipfile.BadZipFile, KeyError) as exc:
        raise InvalidPackageError(
            f"Kein gueltiges XJustiz-Uebermittlung-Paket (ZIP fehlerhaft oder "
            f"'{_XJUSTIZ_MESSAGE_FILENAME}' fehlt): {exc}"
        ) from exc

    try:
        xjustiz.validate_uebermittlung_schriftgutobjekte(message_xml)
    except xjustiz.ValidationError as exc:
        raise InvalidPackageError(f"XJustiz-Nachricht ungueltig: {exc}") from exc

    try:
        parsed = xjustiz.parse_uebermittlung_schriftgutobjekte(message_xml)
    except xjustiz.ParseError as exc:
        raise InvalidPackageError(str(exc)) from exc

    if parsed.akte_anzeigename is None and process_definition_id is not None:
        raise ProcessDefinitionWithoutAkteError(
            "process_definition_id angegeben, aber das Paket enthaelt keine Akte, "
            "deren Anzeigename als Name fuer den neuen Fall dienen koennte"
        )
    if parsed.akte_anzeigename is not None and case_id is None and process_definition_id is None:
        raise CaseTargetRequiredError(
            "Das Paket enthaelt eine Akte - bitte case_id (an bestehenden Fall anhaengen) "
            "oder process_definition_id (neuen Fall anlegen) angeben"
        )

    target_case_id = case_id
    case_created = False
    if process_definition_id is not None:
        attributes = {"xjustiz_herkunft_id": parsed.akte_id} if parsed.akte_id else {}
        case = await case_client.create_case(
            name=parsed.akte_anzeigename,
            process_definition_id=process_definition_id,
            created_by=created_by,
            attributes=attributes,
        )
        target_case_id = case["id"]
        case_created = True

    document_ids: list[str] = []
    for doc in parsed.documents:
        key = f"dokumente/{doc.dateiname}"
        if key not in contents:
            raise InvalidPackageError(
                f"Im Paket referenzierte Datei {doc.dateiname!r} fehlt im ZIP-Archiv"
            )
        created = await document_client.create_document(
            data=contents[key],
            filename=doc.dateiname,
            content_type=None,
            title=doc.dateiname,
            created_by=created_by,
            folder_id=folder_id,
        )
        document_ids.append(created["id"])
        if target_case_id is not None:
            await case_client.add_document_reference(
                target_case_id, document_id=created["id"], added_by=created_by
            )

    return XJustizImportResult(
        case_id=target_case_id,
        case_created=case_created,
        akte_anzeigename=parsed.akte_anzeigename,
        document_ids=document_ids,
    )
