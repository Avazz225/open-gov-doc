"""XDOMEA-4.0.0-Aussonderungsnachricht (5.6, seit P7-S3b, ADR 0029) - baut und
validiert die Nachricht `Aussonderung.Aussonderung.0503` ("Der Export von
Schriftgutobjekten mit dem Ziel der Übergabe an das zuständige Archiv") für
eine geschlossene Umlaufmappe (`Case` -> `xdomea:Vorgang`, ohne umschließende
`Akte` - strukturell laut Schema gültig) mit ihren referenzierten Dokumenten
(`CaseDocumentReference` -> `xdomea:Dokument`). Nur diese eine Nachricht wird
erzeugt, nicht der volle bilaterale 0501-0507-Verhandlungsfluss (siehe
docs/services/archival-service.md).

Jedes Feld hier wurde gegen das echte, in `xdomea_schema/` vendorte
XDOMEA-4.0.0-Schema validiert (nicht spekulativ - siehe Kommentare an den
Stellen, an denen die Struktur überraschend war)."""

import mimetypes
import uuid
from datetime import UTC, datetime

from lxml import etree

from archival_service.xdomea_schema import SCHEMA_DIR

XDOMEA_NS = "urn:xoev-de:xdomea:schema:4.0.0"
_NSMAP = {"xdomea": XDOMEA_NS}
_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_DNS


class ValidationError(Exception):
    """Die erzeugte Nachricht verstößt gegen das echte XDOMEA-4.0.0-Schema."""


def _qn(tag: str) -> str:
    return f"{{{XDOMEA_NS}}}{tag}"


def _deterministic_uuid(*parts: str) -> str:
    """`uuid5` statt `uuid4` - reproduzierbar bei einem Retry desselben
    Transfers (kein neuer UUID bei jedem erneuten Bauversuch)."""
    return str(uuid.uuid5(_UUID_NAMESPACE, ":".join(("dms", *parts))))


def package_filename(document_id: str, version_number: int, content_type: str | None) -> str:
    """`stringDateinameType` (Baukasten.xsd) erzwingt per Regex-Pattern, dass
    ein Primärdokument-Dateiname mit einer UUID beginnt (optional gefolgt von
    `_Name` und/oder einer Endung) - kein reiner `{document_id}_{version}`-
    Name wie ursprünglich angenommen. Deterministisch aus `document_id`/
    `version_number` abgeleitet, damit Wiederholtes Bauen (Retry) denselben
    Namen ergibt."""
    file_uuid = _deterministic_uuid(document_id, str(version_number), "primaerdokument")
    extension = mimetypes.guess_extension(content_type or "") or ""
    return f"{file_uuid}{extension}"


def build_aussonderung_message(case: dict, documents: list[dict]) -> bytes:
    """`documents`: Liste von `{document_id, version_number, content_type,
    original_filename, package_filename}` - `package_filename` muss exakt der
    Datei-Eintrag im später geschnürten ZIP-Paket sein (siehe
    `case_pipeline.py`)."""
    now = datetime.now(UTC)
    message_uuid = str(uuid.uuid4())  # jede Nachricht braucht laut Schema eine NEUE UUID
    prozess_uuid = _deterministic_uuid("case", case["id"], "prozess")

    root = etree.Element(_qn("Aussonderung.Aussonderung.0503"), nsmap=_NSMAP)
    root.set("produkt", "DMS")
    root.set("produkthersteller", "DMS-Projekt")
    root.set("standard", "xdomea")
    root.set("version", "4.0.0")

    kopf = etree.SubElement(root, _qn("nachrichtenkopf"))
    ident_nachricht = etree.SubElement(kopf, _qn("identifikation.nachricht"))
    # Diese drei Felder sind in der Basistyp-Restriktion (bn-uq-g2g, "uq" =
    # unqualified) explizit form="unqualified" - anders als der Rest der
    # Nachricht OHNE xdomea-Namensraum-Präfix, sonst schlägt die Validierung fehl.
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

    # "1"/"0" statt "true"/"false" - der `fixed`-Wertevergleich von
    # Importbestaetigung prüft die exakte lexikalische Form, nicht nur die
    # semantische Gleichheit.
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
        # Immer Code "100" ("Sonstiges") statt einer vollständigen MIME-Typ->
        # XDOMEA-Codeliste-Abbildung (Konzept-Vereinfachung, siehe
        # docs/services/archival-service.md) - `SonstigerName` trägt den
        # tatsächlichen Content-Type.
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
    """Löst die externen `xoev.de`-Imports (Baukasten.xsd) auf die vendorten
    lokalen Dateien auf - kein Netzwerkzugriff zur Laufzeit/in Tests."""

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

    def resolve(self, url, id, context):  # noqa: A002 - lxml-Resolver-Signatur
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
    """Wirft `ValidationError`, falls `xml_bytes` gegen das echte,
    vendorte XDOMEA-4.0.0-Schema nicht valide ist."""
    document = etree.fromstring(xml_bytes)
    try:
        _SCHEMA.assertValid(document)
    except etree.DocumentInvalid as exc:
        raise ValidationError(str(exc)) from exc
