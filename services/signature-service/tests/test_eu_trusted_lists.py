"""EU trusted lists and qualification (P92-S2, ADR 0248) with generated,
XAdES-signed test lists (`testtl.py`): pyHanko's real list parsing and
signature checks run. Pure: no database, no other service."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import testpki
import testtl
from asn1crypto import x509 as asn1_x509
from lxml import etree
from signature_service.validation import trusted_lists
from signature_service.validation.fetcher import build_fenced_session
from signature_service.validation.interface import FetchLimits, ValidationPolicy
from signature_service.validation.pades import PadesValidator

with open(os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf"), "rb") as _f:
    PDF = _f.read()


@pytest.fixture(scope="module")
def eu():
    """A LOTL (signed by the session's test LOTL signer) naming a DE list
    with one qualified CA service."""
    tl_signer = testtl.make_tlso("DE TL signer")
    qualified_ca = testpki.make_cert("Test-QTSP Qualified CA", ca=True)
    tl = testtl.make_tl(qualified_ca=qualified_ca, tlso=tl_signer)
    lotl = testtl.make_lotl(
        lotl_signer=testtl.LOTL_SIGNER, tl_signer=tl_signer, tl_url="https://tl.test/TL-DE.xml"
    )
    return {"tl": tl, "lotl": lotl, "ca": qualified_ca, "tl_signer": tl_signer}


def _signers():
    return [asn1_x509.Certificate.load(testtl.LOTL_SIGNER.der)]


def _policy(registry) -> ValidationPolicy:
    return ValidationPolicy(
        anchors=[],
        revocation_mode="soft-fail",
        outbound_fetching=False,
        max_signatures=20,
        max_path_length=5,
        time_limit_seconds=30,
        fetch=FetchLimits(),
        tsp_registry=registry,
    )


def test_describe_reads_kind_territory_sequence_and_dates(eu):
    lotl = trusted_lists.describe(eu["lotl"])
    tl = trusted_lists.describe(eu["tl"])
    assert (lotl.kind, lotl.territory, lotl.sequence_number) == ("lotl", "EU", 398)
    assert (tl.kind, tl.territory, tl.sequence_number) == ("tl", "DE", 7)
    assert tl.next_update > tl.issued_at
    with pytest.raises(trusted_lists.TrustedListError):
        trusted_lists.describe("<kein-xml")


def test_lotl_and_tl_signatures_are_checked(eu):
    lotl = trusted_lists.check_lotl(eu["lotl"], _signers())
    info, location = trusted_lists.check_tl(eu["tl"], lotl)
    assert info.territory == "DE"
    assert location == "https://tl.test/TL-DE.xml"

    stranger = [asn1_x509.Certificate.load(testtl.make_tlso("Fremd").der)]
    with pytest.raises(trusted_lists.TrustedListError, match="Liste der Listen"):
        trusted_lists.check_lotl(eu["lotl"], stranger)
    # A TL signed by someone the LOTL doesn't name for DE.
    forged = testtl.make_tl(qualified_ca=eu["ca"], tlso=testtl.make_tlso("Fälscher"))
    with pytest.raises(trusted_lists.TrustedListError, match="Signatur der Vertrauensliste DE"):
        trusted_lists.check_tl(forged, lotl)
    with pytest.raises(trusted_lists.TrustedListError, match="keine nationale"):
        trusted_lists.check_tl(eu["lotl"], lotl)


def test_a_list_changed_after_signing_is_refused(eu):
    lotl = trusted_lists.check_lotl(eu["lotl"], _signers())
    root = etree.fromstring(eu["tl"].encode())
    name = root.find(
        ".//{http://uri.etsi.org/02231/v2#}TSPName/{http://uri.etsi.org/02231/v2#}Name"
    )
    name.text = "Untergeschobener Anbieter"
    tampered = etree.tostring(root, encoding="unicode")
    with pytest.raises(trusted_lists.TrustedListError):
        trusted_lists.check_tl(tampered, lotl)


async def _validate(eu, leaf: testpki.Party, issuer: testpki.Party):
    registry, errors = trusted_lists.build_registry([eu["tl"]])
    assert errors == []
    signed = await testpki.sign_pdf(PDF, leaf, [issuer])
    return await PadesValidator().validate(signed, _policy(registry))


async def test_qualified_certificate_on_a_qscd_is_qes(eu):
    leaf = testtl.make_qualified_leaf("Erika Qualifiziert", eu["ca"], qscd=True)
    outcome = await _validate(eu, leaf, eu["ca"])
    [finding] = outcome.findings
    assert finding.indication == "TOTAL-PASSED"
    assert finding.qualification == "qes"
    assert finding.qc_type == "esign"
    assert finding.trust_source == "eu_trusted_list"


async def test_qualified_certificate_without_qscd_is_adesqc(eu):
    leaf = testtl.make_qualified_leaf("Ohne QSCD", eu["ca"], qscd=False)
    [finding] = (await _validate(eu, leaf, eu["ca"])).findings
    assert finding.qualification == "adesqc"


async def test_plain_certificate_from_a_listed_ca_is_ades(eu):
    leaf = testpki.make_cert("Nicht qualifiziert", eu["ca"], ca=False)
    [finding] = (await _validate(eu, leaf, eu["ca"])).findings
    assert finding.indication == "TOTAL-PASSED"
    assert finding.qualification == "ades"
    assert finding.trust_source == "eu_trusted_list"


async def test_ca_not_on_the_list_is_not_trusted(eu):
    other = testpki.make_cert("Nicht gelistete CA", ca=True)
    leaf = testtl.make_qualified_leaf("Behauptet qualifiziert", other)
    [finding] = (await _validate(eu, leaf, other)).findings
    assert finding.indication == "INDETERMINATE"
    assert finding.qualification == "unknown"


async def test_a_run_carries_an_etsi_report(eu):
    leaf = testtl.make_qualified_leaf("Mit Bericht", eu["ca"])
    outcome = await _validate(eu, leaf, eu["ca"])
    assert outcome.report_xml is not None
    report = etree.fromstring(outcome.report_xml.encode())
    assert report.tag.endswith("ValidationReport")
    assert "urn:etsi:019102:mainindication:total-passed" in outcome.report_xml


class _ListServer:
    """Serves the test LOTL and TL over HTTP (a thread, so it answers while a
    synchronous caller waits)."""

    def __init__(self, files: dict[str, str]):
        self.files = files
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                outer.hits.append(self.path)
                body = outer.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def list_server():
    server = _ListServer({})
    yield server
    server.close()


def _publish(
    server: _ListServer, *, tl_signer_named: testpki.Party, tl_signed_by: testpki.Party, ca
):
    tl = testtl.make_tl(qualified_ca=ca, tlso=tl_signed_by)
    lotl = testtl.make_lotl(
        lotl_signer=testtl.LOTL_SIGNER, tl_signer=tl_signer_named, tl_url=f"{server.base}/TL-DE.xml"
    )
    server.files.update({"/eu-lotl.xml": lotl, "/TL-DE.xml": tl})


async def test_fetch_stores_what_passes_and_reports_what_fails(list_server, eu):
    signer = testtl.make_tlso("DE TL signer 2")
    _publish(list_server, tl_signer_named=signer, tl_signed_by=signer, ca=eu["ca"])
    session = build_fenced_session(
        FetchLimits(allow_local_test_server=True), same_host_redirects=False
    )
    try:
        checked, errors = await trusted_lists.fetch_and_check(
            session,
            lotl_url=f"{list_server.base}/eu-lotl.xml",
            signers=_signers(),
            territories=None,
        )
        assert errors == []
        assert [(c.info.kind, c.info.territory) for c in checked] == [("lotl", "EU"), ("tl", "DE")]

        # Now the TL is signed by someone else than the LOTL names.
        _publish(
            list_server,
            tl_signer_named=signer,
            tl_signed_by=testtl.make_tlso("Falsch"),
            ca=eu["ca"],
        )
        checked, errors = await trusted_lists.fetch_and_check(
            session,
            lotl_url=f"{list_server.base}/eu-lotl.xml",
            signers=_signers(),
            territories=None,
        )
        assert [c.info.kind for c in checked] == ["lotl"]
        assert len(errors) == 1 and errors[0].startswith("DE:")

        # Territory filter: DE isn't fetched at all.
        list_server.hits.clear()
        checked, _ = await trusted_lists.fetch_and_check(
            session,
            lotl_url=f"{list_server.base}/eu-lotl.xml",
            signers=_signers(),
            territories={"AT"},
        )
        assert list_server.hits == ["/eu-lotl.xml"]
    finally:
        await session.close()
