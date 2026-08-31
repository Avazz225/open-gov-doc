"""XDOMEA 4.0.0 messages (5.6/14.2, ADR 0029/0126) - builds and validates:

- `Aussonderung.Aussonderung.0503` (since P7-S3b): "the export of records
  with the aim of transfer to the responsible archive" for a closed
  circulation folder (`Case` -> `xdomea:Vorgang`, without an enclosing
  `Akte` - structurally valid per the schema) with its referenced documents
  (`CaseDocumentReference` -> `xdomea:Dokument`). Only this single message is
  generated, not the full bilateral 0501-0507 negotiation flow (see
  docs/services/archival-service.md).
- `Abgabe.Abgabe.0401` (since Post-Roadmap Phase 31 Session 13a): "the
  complete export of records objects upon change of jurisdiction between
  authorities or system changes" - general document/case handoff, NOT tied
  to the disposal pipeline. Reuses the exact same `Vorgang`/
  `DokumentOderDokumentMitSchriftstueck` substructure as the 0503 message
  (shared XDOMEA types, `VorgangType`/`DokumentOderDokumentMitSchriftstueckType`
  in `xdomea-Baukasten.xsd`), wrapped in a `Schriftgutobjekt` choice element
  instead of nested inside a single fixed `Vorgang`, and can carry a
  standalone `Dokument` with no enclosing `Vorgang` at all - the general
  export case a bare document (no case) needs. Only the 0401 message itself
  is generated, not the 0402/0403 import-confirmation counterparts (P31-S13b
  is the IMPORT direction, a separate session).

Every field here was validated against the real XDOMEA 4.0.0 schema vendored
in `xdomea_schema/` (not speculative - see comments at the places where the
structure was surprising)."""

import mimetypes
import uuid
from datetime import UTC, datetime

from lxml import etree

from archival_service.xdomea_schema import SCHEMA_DIR

XDOMEA_NS = "urn:xoev-de:xdomea:schema:4.0.0"
_NSMAP = {"xdomea": XDOMEA_NS}
_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_DNS


class ValidationError(Exception):
    """The generated message violates the real XDOMEA 4.0.0 schema."""


def _qn(tag: str) -> str:
    return f"{{{XDOMEA_NS}}}{tag}"


def _deterministic_uuid(*parts: str) -> str:
    """`uuid5` instead of `uuid4` - reproducible on a retry of the same
    transfer (no new UUID on every rebuild attempt)."""
    return str(uuid.uuid5(_UUID_NAMESPACE, ":".join(("dms", *parts))))


def package_filename(document_id: str, version_number: int, content_type: str | None) -> str:
    """`stringDateinameType` (Baukasten.xsd) enforces via a regex pattern
    that a primary-document filename starts with a UUID (optionally
    followed by `_Name` and/or an extension) - not a plain
    `{document_id}_{version}` name as originally assumed. Deterministically
    derived from `document_id`/`version_number`, so that rebuilding (retry)
    produces the same name."""
    file_uuid = _deterministic_uuid(document_id, str(version_number), "primaerdokument")
    extension = mimetypes.guess_extension(content_type or "") or ""
    return f"{file_uuid}{extension}"


def _build_dokument_wrapper(parent: "etree._Element", document: dict) -> None:
    """Appends one `DokumentOderDokumentMitSchriftstueck` -> `Dokument` to
    `parent` - shared by both the 0503 (nested inside a `Vorgang`) and the
    0401 message (as a standalone `Schriftgutobjekt` choice member, see
    `_build_dokument_schriftgutobjekt`) - `DokumentOderDokumentMitSchriftstueckType`
    is the identical shared XDOMEA type either way. `document`: a
    `{document_id, version_number, content_type, original_filename,
    package_filename}` dict - `package_filename` must exactly match the file
    entry in the ZIP package assembled later."""
    wrapper = etree.SubElement(parent, _qn("DokumentOderDokumentMitSchriftstueck"))
    dokument = etree.SubElement(wrapper, _qn("Dokument"))
    dokument_ident = etree.SubElement(dokument, _qn("Identifikation"))
    etree.SubElement(dokument_ident, _qn("xdomeaUUID")).text = _deterministic_uuid(
        "document", document["document_id"]
    )
    version = etree.SubElement(dokument, _qn("Version"))
    etree.SubElement(version, _qn("Nummer")).text = str(document["version_number"])
    fmt = etree.SubElement(version, _qn("Format"))
    # Always code "100" ("Sonstiges"/other) instead of a full
    # MIME-type-to-XDOMEA-code-list mapping (concept simplification, see
    # docs/services/archival-service.md) - `SonstigerName` carries the
    # actual content type.
    name_el = etree.SubElement(fmt, _qn("Name"))
    name_el.set("listVersionID", "1.0")
    etree.SubElement(name_el, "code").text = "100"
    etree.SubElement(name_el, "name").text = "Sonstiges"
    etree.SubElement(fmt, _qn("SonstigerName")).text = (
        document.get("content_type") or "application/octet-stream"
    )
    etree.SubElement(fmt, _qn("Version")).text = "unbekannt"
    primaerdokument = etree.SubElement(fmt, _qn("Primaerdokument"))
    etree.SubElement(primaerdokument, _qn("Dateiname")).text = document["package_filename"]
    etree.SubElement(primaerdokument, _qn("DateinameOriginal")).text = (
        document.get("original_filename") or document["package_filename"]
    )


def _build_vorgang_aussonderung(
    parent: "etree._Element", case: dict, documents: list[dict]
) -> None:
    """Appends one `Vorgang` (with nested documents) to `parent`, typed per
    `VorgangAussonderungType` (`xdomea-Typen-AussonderungDurchfuehren.xsd`) -
    the 0503 message's `Schriftgutobjekt/Vorgang` is NOT the generic
    `xdomea:VorgangType` (see `_build_vorgang_generic` for that one, used by
    the 0401 message) - `VorgangAussonderungType` additionally requires a
    `Kontextobjekt` element right after `Identifikation`, which the generic
    type does not have at all. Found live during Post-Roadmap Phase 31
    Session 13a's own schema verification for the new 0401 message - this
    function's OWN behavior/output is unchanged from before that session."""
    vorgang = etree.SubElement(parent, _qn("Vorgang"))
    vorgang_ident = etree.SubElement(vorgang, _qn("Identifikation"))
    etree.SubElement(vorgang_ident, _qn("xdomeaUUID")).text = _deterministic_uuid(
        "case", case["id"], "vorgang"
    )
    etree.SubElement(vorgang, _qn("Kontextobjekt")).text = "0"
    allgemeine_metadaten = etree.SubElement(vorgang, _qn("AllgemeineMetadaten"))
    etree.SubElement(allgemeine_metadaten, _qn("Betreff")).text = case["name"]
    for document in documents:
        _build_dokument_wrapper(vorgang, document)


def _build_vorgang_generic(parent: "etree._Element", case: dict, documents: list[dict]) -> None:
    """Appends one `Vorgang` (with nested documents) to `parent`, typed per
    the generic `xdomea:VorgangType` (`xdomea-Baukasten.xsd`) - used by the
    0401 message's `Schriftgutobjekt` choice element. No `Kontextobjekt`
    field (that's specific to `VorgangAussonderungType`, see
    `_build_vorgang_aussonderung`) - `AllgemeineMetadaten` follows
    `Identifikation` directly."""
    vorgang = etree.SubElement(parent, _qn("Vorgang"))
    vorgang_ident = etree.SubElement(vorgang, _qn("Identifikation"))
    etree.SubElement(vorgang_ident, _qn("xdomeaUUID")).text = _deterministic_uuid(
        "case", case["id"], "vorgang"
    )
    allgemeine_metadaten = etree.SubElement(vorgang, _qn("AllgemeineMetadaten"))
    etree.SubElement(allgemeine_metadaten, _qn("Betreff")).text = case["name"]
    for document in documents:
        _build_dokument_wrapper(vorgang, document)


def _build_nachrichtenkopf(
    root: "etree._Element",
    *,
    nachrichtentyp_code: str,
    leser_name: str,
    autor_name: str,
    prozess_uuid: str,
) -> None:
    """Appends the shared `nachrichtenkopf` (message header) structure - both
    the 0503 and 0401 messages use the identical `NachrichtenkopfType`.
    `prozess_uuid` is deliberately a caller-supplied parameter, not derived
    from the fresh per-message `nachrichtenUUID` generated here - it must
    stay stable across a rebuild of the same underlying export (see
    `_deterministic_uuid`'s own docstring), the same reproducibility
    requirement `build_aussonderung_message` already established."""
    kopf = etree.SubElement(root, _qn("nachrichtenkopf"))
    ident_nachricht = etree.SubElement(kopf, _qn("identifikation.nachricht"))
    # These three fields are explicitly form="unqualified" in the base-type
    # restriction (bn-uq-g2g, "uq" = unqualified) - unlike the rest of the
    # message, WITHOUT the xdomea namespace prefix, otherwise validation fails.
    etree.SubElement(ident_nachricht, "nachrichtenUUID").text = str(uuid.uuid4())
    nachrichtentyp = etree.SubElement(ident_nachricht, "nachrichtentyp")
    etree.SubElement(nachrichtentyp, "code").text = nachrichtentyp_code
    etree.SubElement(ident_nachricht, "erstellungszeitpunkt").text = datetime.now(UTC).isoformat(
        timespec="milliseconds"
    )
    leser = etree.SubElement(kopf, _qn("leser"))
    leser_institution = etree.SubElement(leser, _qn("NameInstitution"))
    etree.SubElement(leser_institution, _qn("Name")).text = leser_name
    autor = etree.SubElement(kopf, _qn("autor"))
    autor_institution = etree.SubElement(autor, _qn("NameInstitution"))
    etree.SubElement(autor_institution, _qn("Name")).text = autor_name
    etree.SubElement(kopf, _qn("ProzessID")).text = prozess_uuid


def build_aussonderung_message(case: dict, documents: list[dict]) -> bytes:
    """`documents`: a list of `{document_id, version_number, content_type,
    original_filename, package_filename}` - `package_filename` must exactly
    match the file entry in the ZIP package assembled later (see
    `case_pipeline.py`)."""
    prozess_uuid = _deterministic_uuid("case", case["id"], "prozess")

    root = etree.Element(_qn("Aussonderung.Aussonderung.0503"), nsmap=_NSMAP)
    root.set("produkt", "DMS")
    root.set("produkthersteller", "DMS-Projekt")
    root.set("standard", "xdomea")
    root.set("version", "4.0.0")

    kopf = etree.SubElement(root, _qn("nachrichtenkopf"))
    ident_nachricht = etree.SubElement(kopf, _qn("identifikation.nachricht"))
    etree.SubElement(ident_nachricht, "nachrichtenUUID").text = str(uuid.uuid4())
    nachrichtentyp = etree.SubElement(ident_nachricht, "nachrichtentyp")
    etree.SubElement(nachrichtentyp, "code").text = "0503"
    etree.SubElement(ident_nachricht, "erstellungszeitpunkt").text = datetime.now(UTC).isoformat(
        timespec="milliseconds"
    )
    leser = etree.SubElement(kopf, _qn("leser"))
    leser_institution = etree.SubElement(leser, _qn("NameInstitution"))
    etree.SubElement(leser_institution, _qn("Name")).text = "Archiv"
    autor = etree.SubElement(kopf, _qn("autor"))
    autor_institution = etree.SubElement(autor, _qn("NameInstitution"))
    etree.SubElement(autor_institution, _qn("Name")).text = "DMS"
    etree.SubElement(kopf, _qn("ProzessID")).text = prozess_uuid

    # "1"/"0" instead of "true"/"false" - the `fixed` value comparison of
    # Importbestaetigung checks the exact lexical form, not just semantic
    # equality.
    etree.SubElement(root, _qn("Importbestaetigung")).text = "1"
    etree.SubElement(root, _qn("RueckmeldungArchivkennung")).text = "0"
    etree.SubElement(root, _qn("Empfangsbestaetigung")).text = "0"
    etree.SubElement(root, _qn("LfdNrNachrichtProTyp")).text = "1"
    etree.SubElement(root, _qn("GesamtanzahlNachrichtenProTyp")).text = "1"

    schriftgutobjekt = etree.SubElement(root, _qn("Schriftgutobjekt"))
    _build_vorgang_aussonderung(schriftgutobjekt, case, documents)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def build_abgabe_message_for_case(
    case: dict, documents: list[dict], *, leser_name: str, autor_name: str = "DMS"
) -> bytes:
    """`Abgabe.Abgabe.0401` (14.2, Post-Roadmap Phase 31 Session 13a,
    ADR 0126) - "the complete export of records objects upon change of
    jurisdiction between authorities or system changes", for an arbitrary
    `case-service` Case (NOT tied to the disposal pipeline, unlike
    `build_aussonderung_message` - any case, closed or not, may be
    exported). `leser_name` is the receiving authority's name - unlike the
    0503 message (always "Archiv", a fixed recipient), a general handoff has
    no sensible default reader and must name a real one. `documents`: same
    shape as `build_aussonderung_message`."""
    root = etree.Element(_qn("Abgabe.Abgabe.0401"), nsmap=_NSMAP)
    root.set("produkt", "DMS")
    root.set("produkthersteller", "DMS-Projekt")
    root.set("standard", "xdomea")
    root.set("version", "4.0.0")
    _build_nachrichtenkopf(
        root,
        nachrichtentyp_code="0401",
        leser_name=leser_name,
        autor_name=autor_name,
        prozess_uuid=_deterministic_uuid("case", case["id"], "abgabe-prozess"),
    )
    # NkAbgabeType's own sequence, appended after nachrichtenkopf (see
    # xdomea-Typen-AbgabeDurchfuehren.xsd) - no RueckmeldungArchivkennung
    # here, that field is specific to NkAussonderungType (0503), not part of
    # NkAbgabeType.
    etree.SubElement(root, _qn("Importbestaetigung")).text = "1"
    etree.SubElement(root, _qn("Empfangsbestaetigung")).text = "0"
    etree.SubElement(root, _qn("LfdNrNachrichtProTyp")).text = "1"
    etree.SubElement(root, _qn("GesamtanzahlNachrichtenProTyp")).text = "1"

    schriftgutobjekt = etree.SubElement(root, _qn("Schriftgutobjekt"))
    _build_vorgang_generic(schriftgutobjekt, case, documents)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def build_abgabe_message_for_document(
    document: dict, *, leser_name: str, autor_name: str = "DMS"
) -> bytes:
    """`Abgabe.Abgabe.0401` for a single, standalone `document-service`
    Document with no enclosing case - the `Schriftgutobjekt` choice element
    permits a bare `DokumentOderDokumentMitSchriftstueck` directly (unlike
    the 0503 message, whose `Vorgang` a document can never appear outside
    of). `document`: same shape as one entry of `build_aussonderung_message`'s
    `documents` list."""
    root = etree.Element(_qn("Abgabe.Abgabe.0401"), nsmap=_NSMAP)
    root.set("produkt", "DMS")
    root.set("produkthersteller", "DMS-Projekt")
    root.set("standard", "xdomea")
    root.set("version", "4.0.0")
    _build_nachrichtenkopf(
        root,
        nachrichtentyp_code="0401",
        leser_name=leser_name,
        autor_name=autor_name,
        prozess_uuid=_deterministic_uuid("document", document["document_id"], "abgabe-prozess"),
    )
    etree.SubElement(root, _qn("Importbestaetigung")).text = "1"
    etree.SubElement(root, _qn("Empfangsbestaetigung")).text = "0"
    etree.SubElement(root, _qn("LfdNrNachrichtProTyp")).text = "1"
    etree.SubElement(root, _qn("GesamtanzahlNachrichtenProTyp")).text = "1"

    schriftgutobjekt = etree.SubElement(root, _qn("Schriftgutobjekt"))
    _build_dokument_wrapper(schriftgutobjekt, document)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


class _LocalSchemaResolver(etree.Resolver):
    """Resolves the external `xoev.de` imports (Baukasten.xsd) to the
    vendored local files - no network access at runtime/in tests."""

    _G2G_URL = (
        "http://xoev.de/schemata/basisnachricht/unqualified/g2g/1_1/"
        "xoev-basisnachricht-unqualified-g2g_1.1.xsd"
    )
    _URL_TO_FILENAME = {
        "http://xoev.de/schemata/code/1_0/xoev-code.xsd": "xoev-code.xsd",
        _G2G_URL: "xoev-basisnachricht-unqualified-g2g_1.1.xsd",
        "https://xoev.de/schemata/din/91379/2022-08/din-norm-91379-datatypes.xsd": (
            "din-norm-91379-datatypes.xsd"
        ),
    }

    def resolve(self, url, id, context):  # noqa: A002 - lxml resolver signature
        filename = self._URL_TO_FILENAME.get(url)
        if filename is None:
            return None
        return self.resolve_filename(str(SCHEMA_DIR / filename), context)


def _load_schema(filename: str) -> etree.XMLSchema:
    parser = etree.XMLParser()
    parser.resolvers.add(_LocalSchemaResolver())
    schema_doc = etree.parse(str(SCHEMA_DIR / filename), parser)
    return etree.XMLSchema(schema_doc)


_SCHEMA = _load_schema("xdomea-Nachrichten-AussonderungDurchfuehren.xsd")
_ABGABE_SCHEMA = _load_schema("xdomea-Nachrichten-AbgabeDurchfuehren.xsd")


def validate_message(xml_bytes: bytes) -> None:
    """Raises `ValidationError` if `xml_bytes` (an `Aussonderung.
    Aussonderung.0503` message) is not valid against the real, vendored
    XDOMEA 4.0.0 schema."""
    document = etree.fromstring(xml_bytes)
    try:
        _SCHEMA.assertValid(document)
    except etree.DocumentInvalid as exc:
        raise ValidationError(str(exc)) from exc


def validate_abgabe_message(xml_bytes: bytes) -> None:
    """Raises `ValidationError` if `xml_bytes` (an `Abgabe.Abgabe.0401`
    message) is not valid against the real, vendored XDOMEA 4.0.0 schema."""
    document = etree.fromstring(xml_bytes)
    try:
        _ABGABE_SCHEMA.assertValid(document)
    except etree.DocumentInvalid as exc:
        raise ValidationError(str(exc)) from exc
