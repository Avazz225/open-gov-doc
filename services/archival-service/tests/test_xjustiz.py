import pytest
from archival_service import xjustiz
from lxml import etree


def _document(
    document_id="doc-1", version_number=1, content_type="application/pdf", title="Rechnung"
):
    return {
        "document_id": document_id,
        "version_number": version_number,
        "content_type": content_type,
        "title": title,
        "package_filename": xjustiz.package_filename(
            title, document_id, version_number, content_type
        ),
    }


def test_build_for_document_validates_against_real_xjustiz_schema():
    """Der wertvollste Test dieser Session (14.2, ADR 0129): validiert die
    erzeugte Nachricht gegen das echte, vendorte XJustiz-3.6.2-Schema - keine
    vereinfachte Teilmenge, kein Mock."""
    document = _document()
    xml_bytes = xjustiz.build_uebermittlung_schriftgutobjekte_for_document(
        document, empfaenger_name="Testgericht"
    )

    xjustiz.validate_uebermittlung_schriftgutobjekte(xml_bytes)


def test_build_for_document_has_no_akte():
    document = _document("doc-standalone", 1, "application/pdf")
    xml_bytes = xjustiz.build_uebermittlung_schriftgutobjekte_for_document(
        document, empfaenger_name="Testgericht"
    )

    root = etree.fromstring(xml_bytes)
    ns = {"xjustiz": xjustiz.XJUSTIZ_NS}
    assert root.find(".//xjustiz:akte", ns) is None
    assert len(root.findall(".//xjustiz:dokument", ns)) == 1


def test_build_for_case_validates_against_real_xjustiz_schema():
    case = {"id": "case-1", "name": "Testfall XJustiz"}
    xml_bytes = xjustiz.build_uebermittlung_schriftgutobjekte_for_case(
        case, [_document()], empfaenger_name="Testgericht"
    )

    xjustiz.validate_uebermittlung_schriftgutobjekte(xml_bytes)


def test_build_for_case_with_multiple_documents_and_no_documents():
    case = {"id": "case-2", "name": "Leerer Fall"}

    xjustiz.validate_uebermittlung_schriftgutobjekte(
        xjustiz.build_uebermittlung_schriftgutobjekte_for_case(
            case, [], empfaenger_name="Andere Behoerde"
        )
    )
    xjustiz.validate_uebermittlung_schriftgutobjekte(
        xjustiz.build_uebermittlung_schriftgutobjekte_for_case(
            case,
            [
                _document("doc-1", 1, "application/pdf"),
                _document("doc-2", 3, "text/plain"),
                _document("doc-3", 1, None),
            ],
            empfaenger_name="Andere Behoerde",
        )
    )


def test_build_for_case_contains_anzeigename_and_nests_documents_inside_akte():
    """Regression guard for a real mistake caught during this session's own
    schema verification: documents nest inside `akte/xjustiz.
    fachspezifischeDaten/inhalt/dokument`, NOT as siblings of `akte` at the
    top `schriftgutobjekte` level (unlike XDOMEA's flatter Schriftgutobjekt
    choice)."""
    case = {"id": "case-3", "name": "Aktenzeichen 2026/001"}
    xml_bytes = xjustiz.build_uebermittlung_schriftgutobjekte_for_case(
        case, [_document("doc-1", 1)], empfaenger_name="Bezirksregierung Test"
    )

    root = etree.fromstring(xml_bytes)
    ns = {"xjustiz": xjustiz.XJUSTIZ_NS}
    anzeigename = root.find(".//xjustiz:akte//xjustiz:anzeigename", ns)
    assert anzeigename.text == "Aktenzeichen 2026/001"
    # A document found via a plain `.//dokument` search must actually be
    # nested inside the akte's own `inhalt`, not a sibling of it.
    dokument_parent_path = root.find(".//xjustiz:akte//xjustiz:inhalt/xjustiz:dokument", ns)
    assert dokument_parent_path is not None


def test_build_for_case_uses_the_generic_andere_sonstige_aktentyp_code():
    """`gds.aktentyp`'s "Andere / Sonstige" is code 017, NOT 001 (which is
    "Zivilakte" in that codelist) - confirmed live against the real
    vendored schema, not assumed from `gds.dokumentklasse`'s own code 001
    for the same concept."""
    case = {"id": "case-4", "name": "Testfall"}
    xml_bytes = xjustiz.build_uebermittlung_schriftgutobjekte_for_case(
        case, [], empfaenger_name="X"
    )

    root = etree.fromstring(xml_bytes)
    aktentyp_code = root.find(".//xjustiz:aktentyp/code", {"xjustiz": xjustiz.XJUSTIZ_NS})
    assert aktentyp_code.text == "017"


def test_build_is_deterministic_across_retries():
    document = _document("doc-1", 1)
    first = xjustiz.build_uebermittlung_schriftgutobjekte_for_document(
        document, empfaenger_name="X"
    )
    second = xjustiz.build_uebermittlung_schriftgutobjekte_for_document(
        document, empfaenger_name="X"
    )

    ns = {"xjustiz": xjustiz.XJUSTIZ_NS}
    first_id = etree.fromstring(first).find(".//xjustiz:dokument//xjustiz:id", ns).text
    second_id = etree.fromstring(second).find(".//xjustiz:dokument//xjustiz:id", ns).text
    assert first_id == second_id


def test_validate_raises_on_structurally_invalid_xml():
    invalid = b'<?xml version="1.0"?><NotXJustiz xmlns="http://www.xjustiz.de"/>'

    with pytest.raises(xjustiz.ValidationError):
        xjustiz.validate_uebermittlung_schriftgutobjekte(invalid)


def test_package_filename_follows_the_name_underscore_uuid_convention():
    """XJustiz's own convention is the OPPOSITE order of XDOMEA's
    `package_filename` ("Dokumentname_UUID.Dateiformat" vs. XDOMEA's
    "UUID_Name.ext")."""
    import re

    name = xjustiz.package_filename("Rechnung", "doc-1", 1, "application/pdf")

    assert re.match(
        r"^Rechnung_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{12}\.pdf$",
        name,
    )


def test_package_filename_is_deterministic():
    assert xjustiz.package_filename(
        "Rechnung", "doc-1", 1, "application/pdf"
    ) == xjustiz.package_filename("Rechnung", "doc-1", 1, "application/pdf")
    assert xjustiz.package_filename(
        "Rechnung", "doc-1", 1, "application/pdf"
    ) != xjustiz.package_filename("Rechnung", "doc-1", 2, "application/pdf")


def test_package_filename_sanitizes_unsafe_characters():
    name = xjustiz.package_filename("Schreiben / Klage?!", "doc-1", 1, "application/pdf")
    assert "/" not in name
    assert "?" not in name
    assert "!" not in name
