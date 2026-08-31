"""XJustiz 3.6.2 general document/case export for inter-agency handoff (14.2,
Post-Roadmap Phase 31 Session 13c, ADR 0129) - builds and validates the
`nachricht.gds.uebermittlungSchriftgutobjekte.0005005` message ("Übermittlung
Schriftgutobjekte", the general-purpose, cross-cutting document/file
transmission message defined in XJustiz's own base module, used across every
communication scenario - not tied to any single judicial process type). The
one representative XJustiz message type this session implements, per
ADR 0126's "first vertical slice" scoping (mirrors ADR 0029's own precedent
of shipping exactly one XDOMEA message, not the standard's full scope).

Export only (mirrors P31-S13a's XDOMEA scope) - an XJustiz IMPORT direction,
if ever needed, is a separate future session, not attempted here.

Every field here was validated against the real, vendored XJustiz 3.6.2
schema (`xjustiz_schema/`) - not speculative, see comments at the places
where the structure was surprising (in particular: the required
`xjustizVersion` attribute on `nachrichtenkopf`, and that the "generic/
other" code differs between the `gds.dokumentklasse` and `gds.aktentyp`
codelists - `001` vs `017` - despite both meaning "Andere / Sonstige")."""

import mimetypes
import re
import uuid
from datetime import UTC, datetime

from lxml import etree

from archival_service.xjustiz_schema import SCHEMA_DIR

XJUSTIZ_NS = "http://www.xjustiz.de"
_NSMAP = {"xjustiz": XJUSTIZ_NS}
_XJUSTIZ_VERSION = "3.6.2"
# Deliberately a DIFFERENT UUID namespace than xdomea.py's own
# `_UUID_NAMESPACE` - an XJustiz identifier for a given document and an
# XDOMEA identifier for that same document are different identifier spaces,
# not meant to collide just because they happen to derive from the same
# `document_id`.
_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_DNS
# `gds.dokumentklasse` (Typ3, externally versioned - fetched live via
# xrepository.de's genericode API, not embedded in the vendored XSD) -
# "017" is NOT "Andere / Sonstige" here, see `Code.GDS.Aktentyp` below for
# the contrasting example of why this must never be assumed to line up
# across different codelists.
_DOKUMENTKLASSE_ANDERE_SONSTIGE_CODE = "001"
_DOKUMENTKLASSE_LIST_VERSION = "1.4"
# `gds.aktentyp` (Typ2, embedded enumeration in the vendored XSD) - "017",
# NOT "001" (which is "Zivilakte" in this list) - confirmed by reading the
# actual enumeration, not assumed from `gds.dokumentklasse`'s own code.
_AKTENTYP_ANDERE_SONSTIGE_CODE = "017"
# `gds.bestandteiltyp` "Original" (a file that is the actual, original
# content, not a representation/signature/etc.) - the same concept as
# xdomea.py's own Primaerdokument, just via a differently-shaped codelist.
_BESTANDTEILTYP_ORIGINAL_CODE = "001"


class ValidationError(Exception):
    """The generated message violates the real XJustiz 3.6.2 schema."""


def _qn(tag: str) -> str:
    return f"{{{XJUSTIZ_NS}}}{tag}"


def _deterministic_uuid(*parts: str) -> str:
    """`uuid5` instead of `uuid4` - reproducible on a rebuild of the same
    export, same reasoning as `xdomea.py`'s own helper of the same name."""
    return str(uuid.uuid5(_UUID_NAMESPACE, ":".join(("dms-xjustiz", *parts))))


def package_filename(
    title: str, document_id: str, version_number: int, content_type: str | None
) -> str:
    """XJustiz's own file-naming convention differs from XDOMEA's: "soll nach
    der Syntax 'Dokumentname_UUID.Dateiformat' gebildet werden" (a
    specification RECOMMENDATION, not a schema-enforced pattern restriction
    - unlike XDOMEA's `stringDateinameType`, `dateiname` here is plain
    `din91379:datatypeC`/free text) - `{sanitized_title}_{uuid}.{ext}`,
    the opposite order of `xdomea.package_filename`'s `{uuid}{ext}`."""
    sanitized_title = re.sub(r"[^A-Za-z0-9äöüÄÖÜß_-]", "_", title).strip("_")[:40] or "Dokument"
    file_uuid = _deterministic_uuid(document_id, str(version_number), "primaerdokument")
    extension = mimetypes.guess_extension(content_type or "") or ""
    return f"{sanitized_title}_{file_uuid}{extension}"


def _build_nachrichtenkopf(
    root: "etree._Element", *, empfaenger_name: str, absender_name: str
) -> None:
    kopf = etree.SubElement(root, _qn("nachrichtenkopf"))
    # Required attribute, found live during this session's own schema
    # verification (not in the sequence at all - an `xs:attribute` on
    # `Type.GDS.Nachrichtenkopf`, easy to miss reading only the sequence).
    kopf.set("xjustizVersion", _XJUSTIZ_VERSION)
    etree.SubElement(kopf, _qn("erstellungszeitpunkt")).text = datetime.now(UTC).isoformat(
        timespec="seconds"
    )

    absender = etree.SubElement(kopf, _qn("absender"))
    absender_info = etree.SubElement(absender, _qn("informationen"))
    absender_auswahl = etree.SubElement(absender_info, _qn("auswahl_kommunikationspartner"))
    etree.SubElement(absender_auswahl, _qn("sonstige")).text = absender_name
    etree.SubElement(absender, _qn("eigeneNachrichtenID")).text = str(uuid.uuid4())

    empfaenger = etree.SubElement(kopf, _qn("empfaenger"))
    empfaenger_info = etree.SubElement(empfaenger, _qn("informationen"))
    empfaenger_auswahl = etree.SubElement(empfaenger_info, _qn("auswahl_kommunikationspartner"))
    etree.SubElement(empfaenger_auswahl, _qn("sonstige")).text = empfaenger_name
    # This DMS installation has no concept of the receiving authority's OWN
    # file reference (Aktenzeichen) for a general handoff - "unbekannt" is
    # the schema's own designated choice for exactly this case, not a
    # workaround.
    az_auswahl = etree.SubElement(empfaenger, _qn("auswahl_aktenzeichen"))
    etree.SubElement(az_auswahl, _qn("aktenzeichen.unbekannt")).text = "true"

    herstellerinformation = etree.SubElement(kopf, _qn("herstellerinformation"))
    etree.SubElement(herstellerinformation, _qn("nameDesProdukts")).text = "DMS"
    etree.SubElement(herstellerinformation, _qn("herstellerDesProdukts")).text = "DMS-Projekt"
    etree.SubElement(herstellerinformation, _qn("version")).text = "1.0.0"


def _build_dokument(parent: "etree._Element", document: dict, *, position: int) -> None:
    """`document`: a `{document_id, version_number, content_type, title,
    package_filename}` dict - `package_filename` must exactly match the
    file entry in the ZIP package assembled later, same principle as
    `xdomea._build_dokument_wrapper`. `position` is the required
    `nummerImUebergeordnetenContainer` (1-based, unique within the
    immediately enclosing container - the top-level `schriftgutobjekte` for
    a standalone document, or one `akte`'s own `inhalt` for a case
    document)."""
    dokument = etree.SubElement(parent, _qn("dokument"))
    identifikation = etree.SubElement(dokument, _qn("identifikation"))
    etree.SubElement(identifikation, _qn("id")).text = _deterministic_uuid(
        "document", document["document_id"]
    )
    etree.SubElement(identifikation, _qn("nummerImUebergeordnetenContainer")).text = str(position)

    fachdaten = etree.SubElement(dokument, _qn("xjustiz.fachspezifischeDaten"))
    dokumentklasse = etree.SubElement(fachdaten, _qn("dokumentklasse"))
    dokumentklasse.set("listVersionID", _DOKUMENTKLASSE_LIST_VERSION)
    etree.SubElement(dokumentklasse, "code").text = _DOKUMENTKLASSE_ANDERE_SONSTIGE_CODE
    datei = etree.SubElement(fachdaten, _qn("datei"))
    etree.SubElement(datei, _qn("dateiname")).text = document["package_filename"]
    bestandteil = etree.SubElement(datei, _qn("bestandteil"))
    etree.SubElement(bestandteil, "code").text = _BESTANDTEILTYP_ORIGINAL_CODE


def build_uebermittlung_schriftgutobjekte_for_document(
    document: dict, *, empfaenger_name: str, absender_name: str = "DMS"
) -> bytes:
    """`nachricht.gds.uebermittlungSchriftgutobjekte.0005005` for a single,
    standalone `document-service` Document with no enclosing case -
    `Type.GDS.Schriftgutobjekte`'s `dokument` list permits a bare document
    with no `akte` at all, the XJustiz counterpart to
    `xdomea.build_abgabe_message_for_document`."""
    root = etree.Element(_qn("nachricht.gds.uebermittlungSchriftgutobjekte.0005005"), nsmap=_NSMAP)
    _build_nachrichtenkopf(root, empfaenger_name=empfaenger_name, absender_name=absender_name)
    etree.SubElement(root, _qn("grunddaten"))

    schriftgutobjekte = etree.SubElement(root, _qn("schriftgutobjekte"))
    _build_dokument(schriftgutobjekte, document, position=1)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def build_uebermittlung_schriftgutobjekte_for_case(
    case: dict, documents: list[dict], *, empfaenger_name: str, absender_name: str = "DMS"
) -> bytes:
    """`nachricht.gds.uebermittlungSchriftgutobjekte.0005005` for an
    arbitrary `case-service` Case and its documents - maps to
    `Type.GDS.Akte` (NOT a XDOMEA-style `Vorgang` - XJustiz's own
    structural unit is Akte/Teilakte/Dokument). Documents nest inside
    `akte/xjustiz.fachspezifischeDaten/inhalt/dokument`, not as siblings of
    `akte` at the top `schriftgutobjekte` level - found live during this
    session's own schema verification (an initial draft assumed a flatter,
    XDOMEA-shaped structure and only caught the mistake by reading
    `Type.GDS.Akte`'s own `inhalt` sub-structure in the vendored schema).
    `case["name"]` becomes the Akte's `anzeigename`; `aktentyp` is always
    the generic "Andere / Sonstige" (code `017`, NOT `001` - see the module
    docstring) since this DMS has no concept of XJustiz's specific
    judicial Aktentyp classification."""
    root = etree.Element(_qn("nachricht.gds.uebermittlungSchriftgutobjekte.0005005"), nsmap=_NSMAP)
    _build_nachrichtenkopf(root, empfaenger_name=empfaenger_name, absender_name=absender_name)
    etree.SubElement(root, _qn("grunddaten"))

    schriftgutobjekte = etree.SubElement(root, _qn("schriftgutobjekte"))
    akte = etree.SubElement(schriftgutobjekte, _qn("akte"))
    identifikation = etree.SubElement(akte, _qn("identifikation"))
    etree.SubElement(identifikation, _qn("id")).text = _deterministic_uuid("case", case["id"])
    etree.SubElement(identifikation, _qn("nummerImUebergeordnetenContainer")).text = "1"

    fachdaten = etree.SubElement(akte, _qn("xjustiz.fachspezifischeDaten"))
    aktentyp = etree.SubElement(fachdaten, _qn("aktentyp"))
    etree.SubElement(aktentyp, "code").text = _AKTENTYP_ANDERE_SONSTIGE_CODE
    etree.SubElement(fachdaten, _qn("anzeigename")).text = case["name"]
    aktenzeichen = etree.SubElement(fachdaten, _qn("aktenzeichen"))
    az_auswahl = etree.SubElement(aktenzeichen, _qn("auswahl_aktenzeichen"))
    etree.SubElement(az_auswahl, _qn("aktenzeichen.freitext")).text = case["id"]

    if documents:
        inhalt = etree.SubElement(fachdaten, _qn("inhalt"))
        for position, document in enumerate(documents, start=1):
            _build_dokument(inhalt, document, position=position)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _load_schema() -> etree.XMLSchema:
    """Unlike `xdomea.py`'s `_load_schema` (which needs a custom
    `etree.Resolver` to remap `xoev.de` URLs to vendored local files),
    XJustiz's own `xs:import`/`xs:include` `schemaLocation` values are
    already plain relative filenames (e.g. `din-norm-91379-datatypes.xsd`,
    `xoev-code.xsd`) that resolve directly against files sitting in this
    same vendored directory - no URL remapping needed. Confirmed by
    verifying the full chain compiles with a plain `etree.parse` before
    writing any of this module's other code."""
    schema_doc = etree.parse(str(SCHEMA_DIR / "xjustiz_0005_nachrichten_3_2.xsd"))
    return etree.XMLSchema(schema_doc)


_SCHEMA = _load_schema()


def validate_uebermittlung_schriftgutobjekte(xml_bytes: bytes) -> None:
    """Raises `ValidationError` if `xml_bytes` is not valid against the
    real, vendored XJustiz 3.6.2 schema."""
    document = etree.fromstring(xml_bytes)
    try:
        _SCHEMA.assertValid(document)
    except etree.DocumentInvalid as exc:
        raise ValidationError(str(exc)) from exc
