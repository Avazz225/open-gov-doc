import io
import os

from pyhanko.pdf_utils.reader import PdfFileReader
from signature_service.connectors.interface import SignerInfo
from signature_service.connectors.internal import (
    InternalSelfSignedConnector,
    generate_root_ca,
    issue_tsa_certificate,
)

SAMPLE_PDF_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


def _build_connector() -> InternalSelfSignedConnector:
    """No cross-service dependency (no document-service/auth-service round
    trip needed) - PAdES-B-LTA mechanics (Post-Roadmap Phase 41 Session 1)
    are entirely internal to this connector, so a direct unit test is both
    faster and more precise than going through the HTTP API."""
    ca_certificate_pem, ca_private_key_pem = generate_root_ca()
    tsa_certificate_pem, tsa_private_key_pem = issue_tsa_certificate(
        ca_certificate_pem, ca_private_key_pem
    )
    return InternalSelfSignedConnector(
        ca_certificate_pem, ca_private_key_pem, tsa_certificate_pem, tsa_private_key_pem
    )


def _sample_pdf_bytes() -> bytes:
    with open(SAMPLE_PDF_PATH, "rb") as f:
        return f.read()


async def test_sign_produces_a_real_pades_subfilter_not_plain_pkcs7():
    """Regression test for the silent bug this session fixes: without
    `subfilter=SigSeedSubFilter.PADES`, pyHanko defaults to
    `ADOBE_PKCS7_DETACHED` - a plain PKCS#7 signature, not a real,
    ETSI-PAdES-conformant one, despite the connector's own name/docstring
    claiming PAdES. `use_pades_lta=True` adds a second, separate
    `/DocTimeStamp` field (`/ETSI.RFC3161`, the archive timestamp itself) -
    the actual content signature (`/Sig`, field name `DMSSignature`) is
    the one that must carry the PAdES subfilter."""
    connector = _build_connector()
    signer = SignerInfo(principal_id="alice", display_name="Alice Test", email="alice@example.com")

    signed = await connector.sign(_sample_pdf_bytes(), signer=signer, level="aes")

    reader = PdfFileReader(io.BytesIO(signed.signed_pdf_bytes), strict=False)
    content_signature = next(
        es for es in reader.embedded_signatures if es.field_name == "DMSSignature"
    )
    assert str(content_signature.sig_object["/SubFilter"]) == "/ETSI.CAdES.detached"


async def test_sign_embeds_validation_info_dss():
    """PAdES-B-LT's embedded validation info (CA chain + CRL) shows up as a
    `/DSS` (Document Security Store) entry in the PDF catalog."""
    connector = _build_connector()
    signer = SignerInfo(principal_id="alice", display_name="Alice Test", email="alice@example.com")

    signed = await connector.sign(_sample_pdf_bytes(), signer=signer, level="aes")

    reader = PdfFileReader(io.BytesIO(signed.signed_pdf_bytes), strict=False)
    assert "/DSS" in reader.root


async def test_sign_result_verifies_as_intact_and_trusted():
    connector = _build_connector()
    signer = SignerInfo(principal_id="alice", display_name="Alice Test", email="alice@example.com")

    signed = await connector.sign(_sample_pdf_bytes(), signer=signer, level="aes")
    result = await connector.verify(signed.signed_pdf_bytes)

    assert result.integrity_intact is True
    assert result.trusted is True
    assert result.errors == []


async def test_extend_timestamp_chain_produces_a_still_valid_longer_chain():
    """The actual "long-term archiving" mechanic (3.10): extending the
    archive-timestamp chain must produce PDF bytes that (a) are strictly
    larger (a new timestamp + DSS update was appended) and (b) still
    verify successfully - the whole point of B-LTA is that a document
    stays verifiable across repeated extensions, not just the first
    signature."""
    connector = _build_connector()
    signer = SignerInfo(principal_id="alice", display_name="Alice Test", email="alice@example.com")

    signed = await connector.sign(_sample_pdf_bytes(), signer=signer, level="aes")
    extended_bytes = await connector.extend_timestamp_chain(signed.signed_pdf_bytes)

    assert len(extended_bytes) > len(signed.signed_pdf_bytes)

    result = await connector.verify(extended_bytes)
    assert result.integrity_intact is True
    assert result.trusted is True
    assert result.errors == []


async def test_extend_timestamp_chain_can_be_applied_repeatedly():
    """A B-LTA chain is meant to be extended repeatedly over the years
    (once per `Settings.retimestamp_interval_days`), not just once."""
    connector = _build_connector()
    signer = SignerInfo(principal_id="alice", display_name="Alice Test", email="alice@example.com")

    signed = await connector.sign(_sample_pdf_bytes(), signer=signer, level="aes")
    once_extended = await connector.extend_timestamp_chain(signed.signed_pdf_bytes)
    twice_extended = await connector.extend_timestamp_chain(once_extended)

    assert len(twice_extended) > len(once_extended)
    result = await connector.verify(twice_extended)
    assert result.integrity_intact is True
    assert result.trusted is True
