# ruff: noqa: E501 - XML templates read better unwrapped
"""Generated, XAdES-signed EU trusted lists for tests (P92-S2).

Builds a list of the lists (LOTL) pointing at one national trusted list (TL)
with one qualified CA service, signed with test keys, so the real parsing and
signature checks of pyHanko run in tests - nothing is mocked. Also issues
qualified leaf certificates (QcStatements: compliance, QSCD, eSign type)."""

import base64
from datetime import UTC, datetime, timedelta

import testpki
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from lxml import etree
from pyhanko.sign.ades import qualified_asn1
from signxml import SignatureMethod
from signxml.xades import XAdESSigner

NS = "http://uri.etsi.org/02231/v2#"
EUGENERIC = "http://uri.etsi.org/TrstSvc/TrustedList/TSLType/EUgeneric"
EULISTOFLISTS = "http://uri.etsi.org/TrstSvc/TrustedList/TSLType/EUlistofthelists"
LOTL_RULE = "http://uri.etsi.org/TrstSvc/TrustedList/schemerules/EUlistofthelists"
EUCOMMON = "http://uri.etsi.org/TrstSvc/TrustedList/schemerules/EUcommon"
CA_QC = "http://uri.etsi.org/TrstSvc/Svctype/CA/QC"
GRANTED = "http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/granted"


def _b64(party: testpki.Party) -> str:
    return base64.b64encode(party.der).decode()


def _dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _scheme_information(
    *, list_type: str, territory: str, sequence: int, issued: datetime, extra: str
) -> str:
    return f"""
  <tsl:SchemeInformation>
    <tsl:TSLVersionIdentifier>5</tsl:TSLVersionIdentifier>
    <tsl:TSLSequenceNumber>{sequence}</tsl:TSLSequenceNumber>
    <tsl:TSLType>{list_type}</tsl:TSLType>
    <tsl:SchemeOperatorName><tsl:Name xml:lang="en">Test operator {territory}</tsl:Name></tsl:SchemeOperatorName>
    <tsl:SchemeInformationURI><tsl:URI xml:lang="en">https://example.org/info</tsl:URI></tsl:SchemeInformationURI>
    <tsl:SchemeTerritory>{territory}</tsl:SchemeTerritory>
    {extra}
    <tsl:ListIssueDateTime>{_dt(issued)}</tsl:ListIssueDateTime>
    <tsl:NextUpdate><tsl:dateTime>{_dt(issued + timedelta(days=180))}</tsl:dateTime></tsl:NextUpdate>
  </tsl:SchemeInformation>"""


def _sign(xml: str, signer: testpki.Party) -> str:
    root = etree.fromstring(xml.encode())
    key_pem = signer.key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signed = XAdESSigner(signature_algorithm=SignatureMethod.ECDSA_SHA256).sign(
        root, key=key_pem, cert=[signer.pem.decode()]
    )
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8").decode()


def make_tlso(name: str) -> testpki.Party:
    """A list operator's signing certificate (self-signed is enough: the LOTL
    names the TL's signer, and the test passes the LOTL's signer in)."""
    return testpki.make_cert(name, ca=False)


def make_tl(
    *, qualified_ca: testpki.Party, tlso: testpki.Party, territory: str = "DE", sequence: int = 7
) -> str:
    issued = datetime.now(UTC) - timedelta(days=1)
    providers = f"""
  <tsl:TrustServiceProviderList>
    <tsl:TrustServiceProvider>
      <tsl:TSPInformation><tsl:TSPName><tsl:Name xml:lang="en">Test QTSP</tsl:Name></tsl:TSPName></tsl:TSPInformation>
      <tsl:TSPServices>
        <tsl:TSPService>
          <tsl:ServiceInformation>
            <tsl:ServiceTypeIdentifier>{CA_QC}</tsl:ServiceTypeIdentifier>
            <tsl:ServiceName><tsl:Name xml:lang="en">Test qualified CA</tsl:Name></tsl:ServiceName>
            <tsl:ServiceDigitalIdentity><tsl:DigitalId><tsl:X509Certificate>{_b64(qualified_ca)}</tsl:X509Certificate></tsl:DigitalId></tsl:ServiceDigitalIdentity>
            <tsl:ServiceStatus>{GRANTED}</tsl:ServiceStatus>
            <tsl:StatusStartingTime>{_dt(datetime(2020, 1, 1, tzinfo=UTC))}</tsl:StatusStartingTime>
          </tsl:ServiceInformation>
        </tsl:TSPService>
      </tsl:TSPServices>
    </tsl:TrustServiceProvider>
  </tsl:TrustServiceProviderList>"""
    xml = f"""<tsl:TrustServiceStatusList xmlns:tsl="{NS}" Id="tl-{territory}" TSLTag="http://uri.etsi.org/19612/TSLTag">
{_scheme_information(list_type=EUGENERIC, territory=territory, sequence=sequence, issued=issued, extra="")}
{providers}
</tsl:TrustServiceStatusList>"""
    return _sign(xml, tlso)


def make_lotl(
    *,
    lotl_signer: testpki.Party,
    tl_signer: testpki.Party,
    tl_url: str,
    territory: str = "DE",
    sequence: int = 398,
) -> str:
    issued = datetime.now(UTC) - timedelta(days=1)
    pointers = f"""
    <tsl:PointersToOtherTSL>
      <tsl:OtherTSLPointer>
        <tsl:ServiceDigitalIdentities><tsl:ServiceDigitalIdentity><tsl:DigitalId><tsl:X509Certificate>{_b64(tl_signer)}</tsl:X509Certificate></tsl:DigitalId></tsl:ServiceDigitalIdentity></tsl:ServiceDigitalIdentities>
        <tsl:TSLLocation>{tl_url}</tsl:TSLLocation>
        <tsl:AdditionalInformation>
          <tsl:OtherInformation><tsl:TSLType>{EUGENERIC}</tsl:TSLType></tsl:OtherInformation>
          <tsl:OtherInformation><tsl:SchemeTerritory>{territory}</tsl:SchemeTerritory></tsl:OtherInformation>
          <tsl:OtherInformation><tslx:MimeType xmlns:tslx="http://uri.etsi.org/02231/v2/additionaltypes#">application/vnd.etsi.tsl+xml</tslx:MimeType></tsl:OtherInformation>
          <tsl:OtherInformation><tsl:SchemeTypeCommunityRules><tsl:URI xml:lang="en">{EUCOMMON}</tsl:URI></tsl:SchemeTypeCommunityRules></tsl:OtherInformation>
        </tsl:AdditionalInformation>
      </tsl:OtherTSLPointer>
    </tsl:PointersToOtherTSL>"""
    xml = f"""<tsl:TrustServiceStatusList xmlns:tsl="{NS}" Id="lotl" TSLTag="http://uri.etsi.org/19612/TSLTag">
{_scheme_information(list_type=EULISTOFLISTS, territory="EU", sequence=sequence, issued=issued, extra=pointers)}
</tsl:TrustServiceStatusList>"""
    return _sign(xml, lotl_signer)


def _qc_statements_der(*, qscd: bool) -> bytes:
    statements = [
        qualified_asn1.QcStatement({"statement_id": "qc_compliance"}),
        qualified_asn1.QcStatement(
            {
                "statement_id": "qc_type",
                "statement_info": qualified_asn1.QcCertificateType(["qct_esign"]),
            }
        ),
    ]
    if qscd:
        statements.append(qualified_asn1.QcStatement({"statement_id": "qc_sscd"}))
    return qualified_asn1.QcStatements(statements).dump()


def make_qualified_leaf(cn: str, issuer: testpki.Party, *, qscd: bool = True) -> testpki.Party:
    """A leaf certificate claiming to be qualified (and on a QSCD)."""
    plain = testpki.make_cert(cn, issuer, ca=False)
    builder = (
        x509.CertificateBuilder()
        .subject_name(plain.cert.subject)
        .issuer_name(issuer.cert.subject)
        .public_key(plain.key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(plain.cert.not_valid_before_utc)
        .not_valid_after(plain.cert.not_valid_after_utc)
    )
    for ext in plain.cert.extensions:
        builder = builder.add_extension(ext.value, critical=ext.critical)
    builder = builder.add_extension(
        x509.UnrecognizedExtension(
            x509.ObjectIdentifier("1.3.6.1.5.5.7.1.3"), _qc_statements_der(qscd=qscd)
        ),
        critical=False,
    )
    from cryptography.hazmat.primitives import hashes

    return testpki.Party(builder.sign(issuer.key, hashes.SHA256()), plain.key)


# One LOTL signer per test session: `conftest.py` hands its certificate to the
# app as `DMS_TRUSTED_LIST_LOTL_SIGNERS_PEM` before the app is imported.
LOTL_SIGNER = make_tlso("Test LOTL signer")
