"""Embedded-metadata extraction and mapping (P90-S5, ADR 0258): pure, no DB."""

import io
import zipfile

from document_service.embedded_metadata import MAX_MEMBER_BYTES, apply_mappings, extract
from pypdf import PdfWriter


def make_pdf(metadata: dict) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata(metadata)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


CORE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
  xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Angebot Dachsanierung</dc:title>
  <dc:subject>Bauamt</dc:subject>
  <dc:creator>M. Muster</dc:creator>
  <cp:keywords>Dach, Angebot</cp:keywords>
  <cp:category>Angebot</cp:category>
  <cp:lastModifiedBy>K. Beispiel</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">2024-03-05T08:15:00Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">2024-03-07T10:00:00Z</dcterms:modified>
</cp:coreProperties>"""

APP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
  <Application>Microsoft Office Word</Application>
</Properties>"""

ODF_META = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <office:meta>
    <dc:title>Protokoll</dc:title>
    <meta:initial-creator>A. Autor</meta:initial-creator>
    <dc:creator>B. Bearbeiter</dc:creator>
    <meta:keyword>Sitzung</meta:keyword>
    <meta:keyword>Rat</meta:keyword>
    <meta:creation-date>2023-11-02T09:00:00</meta:creation-date>
    <meta:generator>LibreOffice/7.6</meta:generator>
  </office:meta>
</office:document-meta>"""


def make_zip(members: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_pdf_info_dictionary():
    data = make_pdf(
        {
            "/Title": "Bescheid 2024",
            "/Author": "Stadt Musterhausen",
            "/Keywords": "Bescheid",
            "/CreationDate": "D:20240131093000+01'00'",
            "/Creator": "Writer",
        }
    )
    result = extract(data, "application/pdf")
    assert result["title"] == "Bescheid 2024"
    assert result["author"] == "Stadt Musterhausen"
    assert result["keywords"] == "Bescheid"
    assert result["created"] == "2024-01-31"
    assert result["application"] == "Writer"
    assert "subject" not in result  # empty fields are left out


def test_ooxml_core_and_app_properties():
    data = make_zip(
        {
            "[Content_Types].xml": "<Types/>",
            "docProps/core.xml": CORE_XML,
            "docProps/app.xml": APP_XML,
        }
    )
    # The sniffer may only say "zip": the package layout decides.
    result = extract(data, "application/zip")
    assert result == {
        "title": "Angebot Dachsanierung",
        "subject": "Bauamt",
        "author": "M. Muster",
        "keywords": "Dach, Angebot",
        "category": "Angebot",
        "last_modified_by": "K. Beispiel",
        "created": "2024-03-05",
        "modified": "2024-03-07",
        "application": "Microsoft Office Word",
    }


def test_odf_meta():
    data = make_zip({"mimetype": "application/vnd.oasis.opendocument.text", "meta.xml": ODF_META})
    result = extract(data, "application/vnd.oasis.opendocument.text")
    assert result["title"] == "Protokoll"
    assert result["author"] == "A. Autor"
    assert result["last_modified_by"] == "B. Bearbeiter"
    assert result["keywords"] == "Sitzung, Rat"
    assert result["created"] == "2023-11-02"
    assert result["application"] == "LibreOffice/7.6"


def test_unreadable_or_unsupported_files_have_no_metadata():
    assert extract(b"plain text", "text/plain") == {}
    assert extract(b"%PDF-1.7 broken", "application/pdf") == {}
    assert extract(b"PK\x03\x04 not a zip", "application/zip") == {}
    assert extract(make_zip({"readme.txt": "x"}), "application/zip") == {}


def test_xml_entities_are_refused():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>&a;</dc:title></cp:coreProperties>"
    )
    assert extract(make_zip({"docProps/core.xml": bomb}), "application/zip") == {}


def test_oversized_member_is_skipped():
    padded = CORE_XML.replace("Bauamt", "x" * (MAX_MEMBER_BYTES + 1))
    assert extract(make_zip({"docProps/core.xml": padded}), "application/zip") == {}


def test_apply_mappings_policies():
    extracted = {"title": "Aus Datei", "author": "Autorin", "created": "2024-01-31"}
    mappings = [
        {"source": "title", "attribute": "Betreff", "overwrite": "if_empty"},
        {"source": "author", "attribute": "Verfasser", "overwrite": "always"},
        {"source": "created", "attribute": "Datum", "overwrite": "if_empty"},
        {"source": "subject", "attribute": "Thema", "overwrite": "always"},
    ]
    merged, applied = apply_mappings(
        {"Betreff": "Vom Nutzer", "Verfasser": "Nutzer", "Datum": ""}, extracted, mappings
    )
    assert merged == {"Betreff": "Vom Nutzer", "Verfasser": "Autorin", "Datum": "2024-01-31"}
    assert applied == ["Verfasser", "Datum"]
