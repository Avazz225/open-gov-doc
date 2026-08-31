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


# --- General XDOMEA export (Abgabe.Abgabe.0401, 14.2, Post-Roadmap Phase 31
# Session 13a, ADR 0126) --------------------------------------------------


def test_build_abgabe_message_for_case_validates_against_real_xdomea_schema():
    """Same rationale as the 0503 test above - validated against the real,
    vendored XDOMEA 4.0.0 schema, not a simplified assumption. Found live
    during this session: the 0401 message's `Vorgang` is NOT the same type
    as the 0503 message's (`VorgangType` vs `VorgangAussonderungType`, see
    `xdomea.py`'s `_build_vorgang_generic`/`_build_vorgang_aussonderung`
    docstrings) - this test would have caught that mistake."""
    case = {"id": "case-abgabe-1", "name": "Testvorgang Abgabe"}
    xml_bytes = xdomea.build_abgabe_message_for_case(
        case, [_document()], leser_name="Landesarchiv Test"
    )

    xdomea.validate_abgabe_message(xml_bytes)


def test_build_abgabe_message_for_case_with_multiple_documents_and_no_documents():
    case = {"id": "case-abgabe-2", "name": "Leere Umlaufmappe zur Abgabe"}

    xdomea.validate_abgabe_message(
        xdomea.build_abgabe_message_for_case(case, [], leser_name="Andere Behoerde")
    )
    xdomea.validate_abgabe_message(
        xdomea.build_abgabe_message_for_case(
            case,
            [
                _document("doc-1", 1, "application/pdf"),
                _document("doc-2", 3, "text/plain"),
                _document("doc-3", 1, None),
            ],
            leser_name="Andere Behoerde",
        )
    )


def test_build_abgabe_message_for_case_contains_betreff_leser_and_document_uuids():
    case = {"id": "case-abgabe-3", "name": "Aktenzeichen 2026/002"}
    xml_bytes = xdomea.build_abgabe_message_for_case(
        case, [_document("doc-1", 1)], leser_name="Bezirksregierung Test"
    )

    root = etree.fromstring(xml_bytes)
    ns = {"xdomea": xdomea.XDOMEA_NS}
    assert root.tag == f"{{{xdomea.XDOMEA_NS}}}Abgabe.Abgabe.0401"
    betreff = root.find(".//xdomea:Betreff", ns)
    assert betreff.text == "Aktenzeichen 2026/002"
    leser_name = root.find(".//xdomea:leser//xdomea:Name", ns)
    assert leser_name.text == "Bezirksregierung Test"
    dokumente = root.findall(".//xdomea:Dokument", ns)
    assert len(dokumente) == 1
    # Unlike the 0503 message, the 0401 message's Vorgang has NO
    # Kontextobjekt element at all (different XDOMEA type, see the
    # docstring on `_build_vorgang_generic`).
    assert root.find(".//xdomea:Vorgang/xdomea:Kontextobjekt", ns) is None


def test_build_abgabe_message_for_case_is_deterministic_across_retries():
    case = {"id": "case-abgabe-4", "name": "Wiederholbarkeit Abgabe"}
    documents = [_document("doc-1", 1)]

    first = xdomea.build_abgabe_message_for_case(case, documents, leser_name="X")
    second = xdomea.build_abgabe_message_for_case(case, documents, leser_name="X")

    ns = {"xdomea": xdomea.XDOMEA_NS}
    first_uuid = etree.fromstring(first).find(".//xdomea:Vorgang//xdomea:xdomeaUUID", ns).text
    second_uuid = etree.fromstring(second).find(".//xdomea:Vorgang//xdomea:xdomeaUUID", ns).text
    assert first_uuid == second_uuid
    first_prozess = etree.fromstring(first).find(".//xdomea:ProzessID", ns).text
    second_prozess = etree.fromstring(second).find(".//xdomea:ProzessID", ns).text
    assert first_prozess == second_prozess


def test_build_abgabe_message_for_document_validates_and_has_no_vorgang():
    """A standalone document (no case) - the `Schriftgutobjekt` choice
    element permits a bare `DokumentOderDokumentMitSchriftstueck` directly,
    unlike the 0503 message where a document can only ever appear nested
    inside a `Vorgang`."""
    document = _document("doc-standalone", 1, "application/pdf")
    xml_bytes = xdomea.build_abgabe_message_for_document(document, leser_name="Andere Behoerde")

    xdomea.validate_abgabe_message(xml_bytes)
    root = etree.fromstring(xml_bytes)
    ns = {"xdomea": xdomea.XDOMEA_NS}
    assert root.find(".//xdomea:Vorgang", ns) is None
    assert len(root.findall(".//xdomea:Dokument", ns)) == 1


def test_validate_abgabe_message_raises_on_structurally_invalid_xml():
    invalid = b'<?xml version="1.0"?><NotXdomea xmlns="urn:xoev-de:xdomea:schema:4.0.0"/>'

    with pytest.raises(xdomea.ValidationError):
        xdomea.validate_abgabe_message(invalid)


def test_build_aussonderung_message_still_has_kontextobjekt_and_rueckmeldungarchivkennung():
    """Regression guard for the 0503 message's OWN unchanged behavior after
    this session's refactor split `_build_vorgang` into
    `_build_vorgang_aussonderung`/`_build_vorgang_generic`."""
    case = {"id": "case-regression", "name": "Unveraendert"}
    xml_bytes = xdomea.build_aussonderung_message(case, [_document()])

    root = etree.fromstring(xml_bytes)
    ns = {"xdomea": xdomea.XDOMEA_NS}
    assert root.find(".//xdomea:Vorgang/xdomea:Kontextobjekt", ns).text == "0"
    assert root.find(".//xdomea:RueckmeldungArchivkennung", ns).text == "0"
