"""XDOMEA 4.0.0 records-disposal message (5.6, since P7-S3b, ADR 0029) -
builds and validates the `Aussonderung.Aussonderung.0503` message ("the
export of records with the aim of transfer to the responsible archive") for
a closed circulation folder (`Case` -> `xdomea:Vorgang`, without an
enclosing `Akte` - structurally valid per the schema) with its referenced
documents (`CaseDocumentReference` -> `xdomea:Dokument`). Only this single
message is generated, not the full bilateral 0501-0507 negotiation flow
(see docs/services/archival-service.md).

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


def build_aussonderung_message(case: dict, documents: list[dict]) -> bytes:
    """`documents`: a list of `{document_id, version_number, content_type,
    original_filename, package_filename}` - `package_filename` must exactly
    match the file entry in the ZIP package assembled later (see
    `case_pipeline.py`)."""
    now = datetime.now(UTC)
    message_uuid = str(uuid.uuid4())  # per the schema, every message needs a NEW UUID
    prozess_uuid = _deterministic_uuid("case", case["id"], "prozess")

    root = etree.Element(_qn("Aussonderung.Aussonderung.0503"), nsmap=_NSMAP)
    root.set("produkt", "DMS")
    root.set("produkthersteller", "DMS-Projekt")
    root.set("standard", "xdomea")
    root.set("version", "4.0.0")

    kopf = etree.SubElement(root, _qn("nachrichtenkopf"))
    ident_nachricht = etree.SubElement(kopf, _qn("identifikation.nachricht"))
    # These three fields are explicitly form="unqualified" in the base-type
    # restriction (bn-uq-g2g, "uq" = unqualified) - unlike the rest of the
    # message, WITHOUT the xdomea namespace prefix, otherwise validation fails.
    etree.SubElement(ident_nachricht, "nachrichtenUUID").text = message_uuid
    nachrichtentyp = etree.SubElement(ident_nachricht, "nachrichtentyp")
    etree.SubElement(nachrichtentyp, "code").text = "0503"
    etree.SubElement(ident_nachricht, "erstellungszeitpunkt").text = now.isoformat(
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
    vorgang = etree.SubElement(schriftgutobjekt, _qn("Vorgang"))
    vorgang_ident = etree.SubElement(vorgang, _qn("Identifikation"))
    etree.SubElement(vorgang_ident, _qn("xdomeaUUID")).text = _deterministic_uuid(
        "case", case["id"], "vorgang"
    )
    etree.SubElement(vorgang, _qn("Kontextobjekt")).text = "0"
    allgemeine_metadaten = etree.SubElement(vorgang, _qn("AllgemeineMetadaten"))
    etree.SubElement(allgemeine_metadaten, _qn("Betreff")).text = case["name"]

    for document in documents:
        wrapper = etree.SubElement(vorgang, _qn("DokumentOderDokumentMitSchriftstueck"))
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


def _load_schema() -> etree.XMLSchema:
    parser = etree.XMLParser()
    parser.resolvers.add(_LocalSchemaResolver())
    schema_doc = etree.parse(
        str(SCHEMA_DIR / "xdomea-Nachrichten-AussonderungDurchfuehren.xsd"), parser
    )
    return etree.XMLSchema(schema_doc)


_SCHEMA = _load_schema()


def validate_message(xml_bytes: bytes) -> None:
    """Raises `ValidationError` if `xml_bytes` is not valid against the
    real, vendored XDOMEA 4.0.0 schema."""
    document = etree.fromstring(xml_bytes)
    try:
        _SCHEMA.assertValid(document)
    except etree.DocumentInvalid as exc:
        raise ValidationError(str(exc)) from exc
