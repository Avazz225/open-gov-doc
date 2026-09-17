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


# --- Parsing an Abgabe.Abgabe.0401 message back (XDOMEA import, 14.2,
# Post-Roadmap Phase 31 Session 13b, ADR 0128) -----------------------------


def test_parse_abgabe_message_roundtrips_a_case_export():
    case = {"id": "case-roundtrip-1", "name": "Testfall Roundtrip"}
    documents = [
        _document("doc-1", 1, "application/pdf"),
        _document("doc-2", 2, "text/plain"),
    ]
    xml_bytes = xdomea.build_abgabe_message_for_case(
        case, documents, leser_name="Landesarchiv Test"
    )

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.vorgang_betreff == "Testfall Roundtrip"
    assert parsed.vorgang_xdomea_uuid is not None
    assert [d.dateiname for d in parsed.documents] == [doc["package_filename"] for doc in documents]
    assert parsed.documents[0].original_filename == "Rechnung.pdf"
    assert parsed.documents[0].content_type == "application/pdf"


def test_parse_abgabe_message_roundtrips_an_empty_case_export():
    case = {"id": "case-roundtrip-2", "name": "Leere Umlaufmappe"}
    xml_bytes = xdomea.build_abgabe_message_for_case(case, [], leser_name="Andere Behoerde")

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.vorgang_betreff == "Leere Umlaufmappe"
    assert parsed.documents == []


def test_parse_abgabe_message_roundtrips_a_standalone_document_export():
    document = _document("doc-standalone", 1, "application/pdf")
    xml_bytes = xdomea.build_abgabe_message_for_document(document, leser_name="Andere Behoerde")

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.vorgang_betreff is None
    assert parsed.vorgang_xdomea_uuid is None
    assert len(parsed.documents) == 1
    assert parsed.documents[0].dateiname == document["package_filename"]
    assert parsed.documents[0].original_filename == "Rechnung.pdf"


def test_parse_dokument_element_skips_a_document_without_a_dateiname():
    """A schema-VALID but structurally-unexpected message (e.g. hand-crafted
    or from a third-party system not following this module's own
    `dokumente/<Dateiname>` packaging convention) - `Primaerdokument` itself
    is `minOccurs="0"` per the schema, so a `Dokument` without one is not a
    schema violation. Post-Roadmap Phase 34 Session 4 (ADR 0142) changed this
    from a `ParseError` that rejected the WHOLE package to a per-document
    skip - a real third-party package can legitimately mix documents that do
    and don't carry attached content (e.g. a metadata-only reference to a
    physical record)."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:DokumentOderDokumentMitSchriftstueck>
      <xdomea:Dokument>
        <xdomea:Version>
          <xdomea:Format/>
        </xdomea:Version>
      </xdomea:Dokument>
    </xdomea:DokumentOderDokumentMitSchriftstueck>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.documents == []
    assert parsed.skipped_document_count == 1


def test_parse_dokument_element_skips_a_document_with_no_version_at_all():
    """`DokumentType.Version` is `minOccurs="0"` - a schema-legal, pure
    metadata-only `Dokument` reference with no version history at all
    (e.g. a physical record never digitized)."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:DokumentOderDokumentMitSchriftstueck>
      <xdomea:Dokument/>
    </xdomea:DokumentOderDokumentMitSchriftstueck>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.documents == []
    assert parsed.skipped_document_count == 1


def test_parse_abgabe_message_picks_the_latest_of_several_versions():
    """A genuine third-party version history (`DokumentType.Version` is
    `maxOccurs="unbounded"`) - the latest (last by document order), not the
    first, must win."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:DokumentOderDokumentMitSchriftstueck>
      <xdomea:Dokument>
        <xdomea:Version>
          <xdomea:Nummer>1.0</xdomea:Nummer>
          <xdomea:Format>
            <xdomea:Primaerdokument>
              <xdomea:Dateiname>alt.pdf</xdomea:Dateiname>
            </xdomea:Primaerdokument>
          </xdomea:Format>
        </xdomea:Version>
        <xdomea:Version>
          <xdomea:Nummer>2.0</xdomea:Nummer>
          <xdomea:Format>
            <xdomea:Primaerdokument>
              <xdomea:Dateiname>neu.pdf</xdomea:Dateiname>
            </xdomea:Primaerdokument>
          </xdomea:Format>
        </xdomea:Version>
      </xdomea:Dokument>
    </xdomea:DokumentOderDokumentMitSchriftstueck>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert [d.dateiname for d in parsed.documents] == ["neu.pdf"]
    assert parsed.skipped_document_count == 0


def test_parse_abgabe_message_reads_betreff_from_an_akte_wrapped_vorgang():
    """A genuine third-party 0401 export CAN wrap its `Vorgang`(e) inside an
    `Akte` (`Schriftgutobjekt` is a choice of `Akte`|`Vorgang`|`Dokument`,
    this module's own export never produces the `Akte` variant) - the
    Akte's own Betreff/UUID must be used as the case-name candidate, and its
    nested Vorgang's documents (previously invisible to the direct-child-only
    XPath) must still be found."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:Akte>
      <xdomea:Identifikation>
        <xdomea:xdomeaUUID>11111111-1111-1111-1111-111111111111</xdomea:xdomeaUUID>
      </xdomea:Identifikation>
      <xdomea:AllgemeineMetadaten>
        <xdomea:Betreff>Drittanbieter-Akte</xdomea:Betreff>
      </xdomea:AllgemeineMetadaten>
      <xdomea:Akteninhalt>
        <xdomea:Vorgang>
          <xdomea:Identifikation>
            <xdomea:xdomeaUUID>22222222-2222-2222-2222-222222222222</xdomea:xdomeaUUID>
          </xdomea:Identifikation>
          <xdomea:AllgemeineMetadaten>
            <xdomea:Betreff>Verschachtelter Vorgang</xdomea:Betreff>
          </xdomea:AllgemeineMetadaten>
          <xdomea:DokumentOderDokumentMitSchriftstueck>
            <xdomea:Dokument>
              <xdomea:Version>
                <xdomea:Format>
                  <xdomea:Primaerdokument>
                    <xdomea:Dateiname>verschachtelt.pdf</xdomea:Dateiname>
                  </xdomea:Primaerdokument>
                </xdomea:Format>
              </xdomea:Version>
            </xdomea:Dokument>
          </xdomea:DokumentOderDokumentMitSchriftstueck>
        </xdomea:Vorgang>
      </xdomea:Akteninhalt>
    </xdomea:Akte>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.vorgang_betreff == "Drittanbieter-Akte"
    assert parsed.vorgang_xdomea_uuid == "11111111-1111-1111-1111-111111111111"
    assert [d.dateiname for d in parsed.documents] == ["verschachtelt.pdf"]


def test_parse_abgabe_message_combines_betreffe_of_several_top_level_vorgaenge():
    """`Schriftgutobjekt` is `maxOccurs="unbounded"` - a real 0401 export
    can legitimately carry more than one, each with its own bare `Vorgang`
    and no enclosing `Akte`. All documents must still be collected
    (previously already true), and the Betreffe are combined into one
    display name rather than silently keeping only the first (previous
    behavior) - no single UUID can represent several distinct Vorgänge."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:Vorgang>
      <xdomea:Identifikation>
        <xdomea:xdomeaUUID>33333333-3333-3333-3333-333333333333</xdomea:xdomeaUUID>
      </xdomea:Identifikation>
      <xdomea:AllgemeineMetadaten>
        <xdomea:Betreff>Erster Vorgang</xdomea:Betreff>
      </xdomea:AllgemeineMetadaten>
      <xdomea:DokumentOderDokumentMitSchriftstueck>
        <xdomea:Dokument>
          <xdomea:Version>
            <xdomea:Format>
              <xdomea:Primaerdokument>
                <xdomea:Dateiname>eins.pdf</xdomea:Dateiname>
              </xdomea:Primaerdokument>
            </xdomea:Format>
          </xdomea:Version>
        </xdomea:Dokument>
      </xdomea:DokumentOderDokumentMitSchriftstueck>
    </xdomea:Vorgang>
  </xdomea:Schriftgutobjekt>
  <xdomea:Schriftgutobjekt>
    <xdomea:Vorgang>
      <xdomea:Identifikation>
        <xdomea:xdomeaUUID>44444444-4444-4444-4444-444444444444</xdomea:xdomeaUUID>
      </xdomea:Identifikation>
      <xdomea:AllgemeineMetadaten>
        <xdomea:Betreff>Zweiter Vorgang</xdomea:Betreff>
      </xdomea:AllgemeineMetadaten>
      <xdomea:DokumentOderDokumentMitSchriftstueck>
        <xdomea:Dokument>
          <xdomea:Version>
            <xdomea:Format>
              <xdomea:Primaerdokument>
                <xdomea:Dateiname>zwei.pdf</xdomea:Dateiname>
              </xdomea:Primaerdokument>
            </xdomea:Format>
          </xdomea:Version>
        </xdomea:Dokument>
      </xdomea:DokumentOderDokumentMitSchriftstueck>
    </xdomea:Vorgang>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.vorgang_betreff == "Erster Vorgang; Zweiter Vorgang"
    assert parsed.vorgang_xdomea_uuid is None
    assert sorted(d.dateiname for d in parsed.documents) == ["eins.pdf", "zwei.pdf"]


def test_parse_abgabe_message_falls_back_to_teilvorgang_betreff_when_akte_betreff_is_empty():
    """Post-Roadmap Phase 42 Session 1 (ADR 0142's own flagged gap, closed
    here): a top-level `Akte` with no `AllgemeineMetadaten` of its own (a
    schema-legal but empty Betreff, `AllgemeineMetadaten` is `minOccurs="0"`)
    must not leave the whole package unnamed when a nested `Teilvorgang`
    (a distinct element name from `Vorgang`, per `VorgangType`'s own
    recursive `Teilvorgang` child) carries a real Betreff."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:Akte>
      <xdomea:Identifikation>
        <xdomea:xdomeaUUID>55555555-5555-5555-5555-555555555555</xdomea:xdomeaUUID>
      </xdomea:Identifikation>
      <xdomea:Akteninhalt>
        <xdomea:Vorgang>
          <xdomea:Identifikation>
            <xdomea:xdomeaUUID>66666666-6666-6666-6666-666666666666</xdomea:xdomeaUUID>
          </xdomea:Identifikation>
          <xdomea:Teilvorgang>
            <xdomea:Identifikation>
              <xdomea:xdomeaUUID>77777777-7777-7777-7777-777777777777</xdomea:xdomeaUUID>
            </xdomea:Identifikation>
            <xdomea:AllgemeineMetadaten>
              <xdomea:Betreff>Teilvorgang-Betreff</xdomea:Betreff>
            </xdomea:AllgemeineMetadaten>
            <xdomea:DokumentOderDokumentMitSchriftstueck>
              <xdomea:Dokument>
                <xdomea:Version>
                  <xdomea:Format>
                    <xdomea:Primaerdokument>
                      <xdomea:Dateiname>teilvorgang.pdf</xdomea:Dateiname>
                    </xdomea:Primaerdokument>
                  </xdomea:Format>
                </xdomea:Version>
              </xdomea:Dokument>
            </xdomea:DokumentOderDokumentMitSchriftstueck>
          </xdomea:Teilvorgang>
        </xdomea:Vorgang>
      </xdomea:Akteninhalt>
    </xdomea:Akte>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.vorgang_betreff == "Teilvorgang-Betreff"
    assert parsed.vorgang_xdomea_uuid is None
    assert [d.dateiname for d in parsed.documents] == ["teilvorgang.pdf"]


def test_parse_abgabe_message_does_not_use_teilakte_fallback_when_a_normal_betreff_exists():
    """The Teilvorgang/Teilakte fallback must only activate when the
    primary candidate's Betreff is genuinely empty - not override an
    already-usable Vorgang/Akte name."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:Vorgang>
      <xdomea:Identifikation>
        <xdomea:xdomeaUUID>88888888-8888-8888-8888-888888888888</xdomea:xdomeaUUID>
      </xdomea:Identifikation>
      <xdomea:AllgemeineMetadaten>
        <xdomea:Betreff>Regulaerer Vorgang</xdomea:Betreff>
      </xdomea:AllgemeineMetadaten>
      <xdomea:Teilvorgang>
        <xdomea:Identifikation>
          <xdomea:xdomeaUUID>99999999-9999-9999-9999-999999999999</xdomea:xdomeaUUID>
        </xdomea:Identifikation>
        <xdomea:AllgemeineMetadaten>
          <xdomea:Betreff>Sollte ignoriert werden</xdomea:Betreff>
        </xdomea:AllgemeineMetadaten>
      </xdomea:Teilvorgang>
    </xdomea:Vorgang>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.vorgang_betreff == "Regulaerer Vorgang"
    assert parsed.vorgang_xdomea_uuid == "88888888-8888-8888-8888-888888888888"


def test_parse_abgabe_message_imports_schriftstueck_content_of_a_dokument_mit_schriftstueck():
    """Post-Roadmap Phase 42 Session 1 (ADR 0142's other flagged gap): a
    `DokumentMitSchriftstueck` (physical/paper-page record) with a real
    scanned `Schriftstueck` must actually be imported, not silently
    ignored - `DokumentMitSchriftstueckType` replaces `Dokument`'s own
    `Version` with 0..N `Schriftstueck` children, each itself a full
    `DokumentType` with the identical `Version`/`Format`/`Primaerdokument`
    shape."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:DokumentOderDokumentMitSchriftstueck>
      <xdomea:DokumentMitSchriftstueck>
        <xdomea:Identifikation>
          <xdomea:xdomeaUUID>aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa</xdomea:xdomeaUUID>
        </xdomea:Identifikation>
        <xdomea:Schriftstueck>
          <xdomea:Version>
            <xdomea:Format>
              <xdomea:Primaerdokument>
                <xdomea:Dateiname>seite1.pdf</xdomea:Dateiname>
              </xdomea:Primaerdokument>
            </xdomea:Format>
          </xdomea:Version>
        </xdomea:Schriftstueck>
        <xdomea:Schriftstueck>
          <xdomea:Version>
            <xdomea:Format>
              <xdomea:Primaerdokument>
                <xdomea:Dateiname>seite2.pdf</xdomea:Dateiname>
              </xdomea:Primaerdokument>
            </xdomea:Format>
          </xdomea:Version>
        </xdomea:Schriftstueck>
      </xdomea:DokumentMitSchriftstueck>
    </xdomea:DokumentOderDokumentMitSchriftstueck>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert sorted(d.dateiname for d in parsed.documents) == ["seite1.pdf", "seite2.pdf"]
    assert parsed.skipped_schriftstueck_count == 0
    assert parsed.skipped_document_count == 0


def test_parse_abgabe_message_counts_a_contentless_schriftstueck_separately_from_dokument():
    """A `Schriftstueck` with no retrievable content (schema-legal, same
    tolerance as a plain contentless `Dokument`) is skipped and counted in
    its OWN counter - `skipped_schriftstueck_count`, not
    `skipped_document_count`, since it's a structurally different reason
    (inherently physical, not a digital document simply missing its
    content)."""
    xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Abgabe.Abgabe.0401 xmlns:xdomea="urn:xoev-de:xdomea:schema:4.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:DokumentOderDokumentMitSchriftstueck>
      <xdomea:DokumentMitSchriftstueck>
        <xdomea:Identifikation>
          <xdomea:xdomeaUUID>bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb</xdomea:xdomeaUUID>
        </xdomea:Identifikation>
        <xdomea:Schriftstueck>
          <xdomea:Version>
            <xdomea:Format/>
          </xdomea:Version>
        </xdomea:Schriftstueck>
      </xdomea:DokumentMitSchriftstueck>
    </xdomea:DokumentOderDokumentMitSchriftstueck>
  </xdomea:Schriftgutobjekt>
</xdomea:Abgabe.Abgabe.0401>"""

    parsed = xdomea.parse_abgabe_message(xml_bytes)

    assert parsed.documents == []
    assert parsed.skipped_schriftstueck_count == 1
    assert parsed.skipped_document_count == 0
