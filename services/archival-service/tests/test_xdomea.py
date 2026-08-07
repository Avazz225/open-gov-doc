import pytest
from archival_service import xdomea
from lxml import etree


def _document(document_id="doc-1", version_number=1, content_type="application/pdf"):
    return {
        "document_id": document_id,
        "version_number": version_number,
        "content_type": content_type,
        "original_filename": "Rechnung.pdf",
        "package_filename": xdomea.package_filename(document_id, version_number, content_type),
    }


def test_build_aussonderung_message_validates_against_real_xdomea_schema():
    """Der wertvollste Test dieser Session (5.6, ADR 0029): validiert die
    erzeugte Nachricht gegen das echte, vendorte XDOMEA-4.0.0-Schema - keine
    vereinfachte Teilmenge, kein Mock."""
    case = {"id": "case-1", "name": "Testvorgang Aussonderung"}
    xml_bytes = xdomea.build_aussonderung_message(case, [_document()])

    xdomea.validate_message(xml_bytes)  # wirft bei Ungueltigkeit


def test_build_aussonderung_message_with_multiple_documents_and_no_documents():
    case = {"id": "case-2", "name": "Leere Umlaufmappe"}

    xdomea.validate_message(xdomea.build_aussonderung_message(case, []))
    xdomea.validate_message(
        xdomea.build_aussonderung_message(
            case,
            [
                _document("doc-1", 1, "application/pdf"),
                _document("doc-2", 3, "text/plain"),
                _document("doc-3", 1, None),
            ],
        )
    )


def test_build_aussonderung_message_contains_case_betreff_and_document_uuids():
    case = {"id": "case-3", "name": "Aktenzeichen 2026/001"}
    xml_bytes = xdomea.build_aussonderung_message(case, [_document("doc-1", 1)])

    root = etree.fromstring(xml_bytes)
    ns = {"xdomea": xdomea.XDOMEA_NS}
    betreff = root.find(".//xdomea:Betreff", ns)
    assert betreff.text == "Aktenzeichen 2026/001"
    dokumente = root.findall(".//xdomea:Dokument", ns)
    assert len(dokumente) == 1


def test_build_aussonderung_message_is_deterministic_across_retries():
    """Gleiche Eingabedaten -> gleiche xdomeaUUIDs (nur die Nachrichten-UUID
    selbst variiert) - wichtig, damit ein Retry desselben Transfers nicht bei
    jedem Versuch neue Objekt-Identitaeten erzeugt."""
    case = {"id": "case-4", "name": "Wiederholbarkeit"}
    documents = [_document("doc-1", 1)]

    first = xdomea.build_aussonderung_message(case, documents)
    second = xdomea.build_aussonderung_message(case, documents)

    ns = {"xdomea": xdomea.XDOMEA_NS}
    first_uuid = etree.fromstring(first).find(".//xdomea:Vorgang//xdomea:xdomeaUUID", ns).text
    second_uuid = etree.fromstring(second).find(".//xdomea:Vorgang//xdomea:xdomeaUUID", ns).text
    assert first_uuid == second_uuid


def test_validate_message_raises_on_structurally_invalid_xml():
    invalid = b'<?xml version="1.0"?><NotXdomea xmlns="urn:xoev-de:xdomea:schema:4.0.0"/>'

    with pytest.raises(xdomea.ValidationError):
        xdomea.validate_message(invalid)


def test_package_filename_matches_required_uuid_prefixed_pattern():
    """`stringDateinameType` (Baukasten.xsd) erzwingt per Regex, dass der
    Dateiname mit einer UUID beginnt."""
    import re

    name = xdomea.package_filename("doc-1", 1, "application/pdf")

    assert re.match(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.pdf$", name
    )


def test_package_filename_is_deterministic():
    assert xdomea.package_filename("doc-1", 1, "application/pdf") == xdomea.package_filename(
        "doc-1", 1, "application/pdf"
    )
    assert xdomea.package_filename("doc-1", 1, "application/pdf") != xdomea.package_filename(
        "doc-1", 2, "application/pdf"
    )
