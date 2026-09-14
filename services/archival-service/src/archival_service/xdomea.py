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
  to the disposal pipeline. Reuses the exact same
  `DokumentOderDokumentMitSchriftstueckType` substructure as the 0503
  message, wrapped in a `Schriftgutobjekt` choice element instead of nested
  inside a single fixed `Vorgang`, and can carry a standalone `Dokument`
  with no enclosing `Vorgang` at all - the general export case a bare
  document (no case) needs. Only the 0401 message itself is built/emitted,
  not the 0402/0403 import-confirmation counterparts. Since Session 13b,
  `parse_abgabe_message` reads a 0401 message back (the IMPORT direction) -
  originally scoped to the exact package shape THIS module's own export
  produces (one `dokumente/<Dateiname>` ZIP entry per `Primaerdokument`,
  `Schriftgutobjekt` containing at most one `Vorgang`), hardened in
  Post-Roadmap Phase 34 Session 4 (ADR 0142) for genuine third-party
  packages: an `Akte`-wrapped `Vorgang` hierarchy, more than one top-level
  `Vorgang`, a document's version history (picks the latest, not the
  first), and a `Dokument` with no attached primary-document content
  (skipped, not a rejected whole-package import) - see that function's and
  `ParsedAbgabeMessage`'s own docstrings for the exact, still bounded scope.

Every field here was validated against the real XDOMEA 4.0.0 schema vendored
in `xdomea_schema/` (not speculative - see comments at the places where the
structure was surprising)."""

import mimetypes
import uuid
from dataclasses import dataclass, field
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


class ParseError(Exception):
    """The message is schema-valid XML but is structurally unreadable in a
    way `parse_abgabe_message` cannot route around."""


@dataclass
class ParsedAbgabeDocument:
    dateiname: str
    """Matches the ZIP entry `dokumente/{dateiname}` (`Primaerdokument/Dateiname`)."""
    original_filename: str
    content_type: str | None


@dataclass
class ParsedAbgabeMessage:
    vorgang_betreff: str | None
    """`None` if the package contains no `Vorgang`/`Akte` at all (a
    standalone document export, see `build_abgabe_message_for_document`).
    Populated from, in priority order (Post-Roadmap Phase 34 Session 4, see
    ADR 0142): (1) a top-level `Akte`'s own `AllgemeineMetadaten/Betreff` -
    this module's own export never produces an `Akte` `Schriftgutobjekt`
    (see the module docstring), but the schema's own
    `Schriftgutobjekt`-choice (`Akte` | `Vorgang` | `Dokument`) means a real
    third-party 0401 export legitimately can, nesting its `Vorgang`s inside
    `Akteninhalt`; (2) a single `Vorgang`'s own Betreff, found ANYWHERE under
    a `Schriftgutobjekt` (not just as its direct child - also covers a
    `Vorgang` nested inside an `Akte`'s `Akteninhalt`, the previous
    direct-child-only XPath missed this entirely); (3) for more than one
    `Vorgang` and no enclosing `Akte`, every found Betreff joined with
    `"; "` - this project's Case model maps 1:1 to a single Vorgang, so
    several structurally-independent Vorgänge collapse into one combined
    display name instead of either silently keeping only the first
    (previous behavior) or rejecting the whole package outright (a real
    0401 export can carry more than one `Schriftgutobjekt`, each with its
    own `Vorgang` - `xdomea-Nachrichten-AbgabeDurchfuehren.xsd`'s own
    `Schriftgutobjekt` is `maxOccurs="unbounded"`)."""
    vorgang_xdomea_uuid: str | None
    """`None` whenever `vorgang_betreff` was built from more than one
    `Vorgang` (case 3 above) - no single UUID can represent several
    structurally-independent Vorgänge."""
    documents: list[ParsedAbgabeDocument] = field(default_factory=list)
    skipped_document_count: int = 0
    """`Dokument` elements found under a `Schriftgutobjekt` that were
    structurally present but had no retrievable primary-document content (no
    `Version` at all, or none of its `Version` entries carry a `Format` with
    a `Primaerdokument`/`Dateiname`) - schema-legal (`DokumentType.Version`
    is `minOccurs="0"`, e.g. a metadata-only reference to a physical record
    that was never digitized), so these are SKIPPED rather than failing the
    whole package import (Post-Roadmap Phase 34 Session 4, see ADR 0142) -
    the previous behavior raised `ParseError` (surfaced as a `422`) and
    rejected an entire genuine third-party package over a single such
    document. `DokumentMitSchriftstueck` (the schema's other
    `DokumentOderDokumentMitSchriftstueck` choice member, a document with
    nested physical-page/Schriftstück scans) remains entirely unsupported -
    genuinely out of this session's bounded scope, not counted here either."""


def _vorgang_name(el: "etree._Element", ns: dict) -> tuple[str | None, str | None]:
    """Betreff/xdomeaUUID of a `Vorgang` OR `Akte` element - both types share
    the identical `AllgemeineMetadaten`/`Identifikation` substructure."""
    betreff_el = el.find("./xdomea:AllgemeineMetadaten/xdomea:Betreff", ns)
    uuid_el = el.find("./xdomea:Identifikation/xdomea:xdomeaUUID", ns)
    return (
        betreff_el.text if betreff_el is not None else None,
        uuid_el.text if uuid_el is not None else None,
    )


def _parse_dokument_element(dokument_el: "etree._Element") -> ParsedAbgabeDocument | None:
    """Returns `None` (not `ParseError`) for a schema-legal `Dokument` with
    no retrievable primary-document content - see
    `ParsedAbgabeMessage.skipped_document_count`'s docstring. Among multiple
    `Version` entries (a genuine version history, `DokumentType.Version` is
    `maxOccurs="unbounded"`), picks the LAST one (by document order) that
    carries a `Format`/`Primaerdokument` - the previous code blindly used a
    single `find()` on the FIRST `Version`/`Format`, silently importing a
    document's oldest content instead of its most recent whenever a real
    third-party package actually carried version history."""
    ns = {"xdomea": XDOMEA_NS}
    format_el = None
    for version_el in reversed(dokument_el.findall("./xdomea:Version", ns)):
        format_el = version_el.find("./xdomea:Format", ns)
        if format_el is not None:
            break
    if format_el is None:
        return None
    primaerdokument_el = format_el.find("./xdomea:Primaerdokument", ns)
    if primaerdokument_el is None:
        return None
    dateiname_el = primaerdokument_el.find("./xdomea:Dateiname", ns)
    if dateiname_el is None or not dateiname_el.text:
        return None
    original_el = primaerdokument_el.find("./xdomea:DateinameOriginal", ns)
    sonstiger_name_el = format_el.find("./xdomea:SonstigerName", ns)
    return ParsedAbgabeDocument(
        dateiname=dateiname_el.text,
        original_filename=(
            original_el.text if original_el is not None and original_el.text else dateiname_el.text
        ),
        content_type=sonstiger_name_el.text if sonstiger_name_el is not None else None,
    )


def parse_abgabe_message(xml_bytes: bytes) -> ParsedAbgabeMessage:
    """Reads an `Abgabe.Abgabe.0401` message back (14.2, Post-Roadmap Phase
    31 Session 13b, hardened for genuine third-party packages in Post-Roadmap
    Phase 34 Session 4, ADR 0142) - the IMPORT direction, the mirror of
    `build_abgabe_message_for_case`/`build_abgabe_message_for_document`.
    Does NOT re-validate against the schema (call `validate_abgabe_message`
    first, same two-step pattern as `general_export.py`'s own build/validate
    calls) and deliberately does not attempt to be a general-purpose XDOMEA
    reader: it looks for `Dokument` elements ANYWHERE under a
    `Schriftgutobjekt` (covers a `Vorgang`'s nested documents, an `Akte`'s
    nested documents/Vorgänge's documents, and a standalone top-level
    `Dokument`, but deliberately excludes the optional `Anschreiben`
    cover-letter element, a sibling of `Schriftgutobjekt`, not a descendant).
    See `ParsedAbgabeMessage.vorgang_betreff`'s docstring for the
    Akte/multi-Vorgang naming priority and
    `ParsedAbgabeMessage.skipped_document_count`'s for the tolerant
    per-document handling. Still deliberately bounded, not a claim of full
    third-party-XDOMEA-package generality: `Teilvorgang`/`Teilakte` (nested
    sub-Vorgänge/sub-Akten, a distinct element name from `Vorgang`/`Akte`
    even though they share the same type) are not walked for their OWN
    Betreff/UUID - only their nested `Dokument` elements are still picked up
    via the unconditional `//Dokument` descendant search above, so no
    document is silently dropped, but a package whose ONLY Vorgang-like
    content lives inside a `Teilvorgang` gets no case name candidate from it."""
    root = etree.fromstring(xml_bytes)
    ns = {"xdomea": XDOMEA_NS}

    akte_els = root.findall(".//xdomea:Schriftgutobjekt/xdomea:Akte", ns)
    vorgang_els = root.findall(".//xdomea:Schriftgutobjekt//xdomea:Vorgang", ns)

    vorgang_betreff: str | None = None
    vorgang_xdomea_uuid: str | None = None
    if akte_els:
        vorgang_betreff, vorgang_xdomea_uuid = _vorgang_name(akte_els[0], ns)
    elif len(vorgang_els) == 1:
        vorgang_betreff, vorgang_xdomea_uuid = _vorgang_name(vorgang_els[0], ns)
    elif vorgang_els:
        betreffe = [
            betreff for betreff, _ in (_vorgang_name(el, ns) for el in vorgang_els) if betreff
        ]
        vorgang_betreff = "; ".join(betreffe) if betreffe else None
        vorgang_xdomea_uuid = None

    documents: list[ParsedAbgabeDocument] = []
    skipped_document_count = 0
    for dokument_el in root.findall(".//xdomea:Schriftgutobjekt//xdomea:Dokument", ns):
        parsed_document = _parse_dokument_element(dokument_el)
        if parsed_document is None:
            skipped_document_count += 1
        else:
            documents.append(parsed_document)

    return ParsedAbgabeMessage(
        vorgang_betreff=vorgang_betreff,
        vorgang_xdomea_uuid=vorgang_xdomea_uuid,
        documents=documents,
        skipped_document_count=skipped_document_count,
    )
