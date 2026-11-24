"""A generated test PKI for incoming-signature validation (P92-S1).

Real qualified signatures can't be used in tests (ADR 0248), so every case
builds its own root CA, an optional intermediate, leaf certificates with CRL
distribution points, and PDFs signed with them. Imported by the validation
tests via `sys.path` (see `conftest.py`)."""

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers
from pyhanko_certvalidator.registry import SimpleCertificateStore


@dataclass
class Party:
    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey

    @property
    def der(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.DER)

    @property
    def pem(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.PEM)

    def asn1(self) -> asn1_x509.Certificate:
        return asn1_x509.Certificate.load(self.der)

    def asn1_key(self) -> asn1_keys.PrivateKeyInfo:
        return asn1_keys.PrivateKeyInfo.load(
            self.key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def make_cert(
    cn: str,
    issuer: Party | None = None,
    *,
    ca: bool,
    crl_url: str | None = None,
    aia_url: str | None = None,
) -> Party:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(issuer.cert.subject if issuer else _name(cn))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=not ca,
                content_commitment=not ca,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if crl_url:
        builder = builder.add_extension(
            x509.CRLDistributionPoints(
                [
                    x509.DistributionPoint(
                        [x509.UniformResourceIdentifier(crl_url)], None, None, None
                    )
                ]
            ),
            critical=False,
        )
    if aia_url:
        builder = builder.add_extension(
            x509.AuthorityInformationAccess(
                [
                    x509.AccessDescription(
                        x509.oid.AuthorityInformationAccessOID.CA_ISSUERS,
                        x509.UniformResourceIdentifier(aia_url),
                    )
                ]
            ),
            critical=False,
        )
    cert = builder.sign((issuer.key if issuer else key), hashes.SHA256())
    return Party(cert, key)


def make_crl(issuer: Party, revoked: list[Party]) -> bytes:
    now = datetime.now(UTC)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer.cert.subject)
        .last_update(now - timedelta(minutes=5))
        .next_update(now + timedelta(days=7))
    )
    for party in revoked:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(party.cert.serial_number)
            .revocation_date(now - timedelta(hours=1))
            .build()
        )
    return builder.sign(issuer.key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


async def sign_pdf(
    pdf: bytes, signer: Party, chain: list[Party], *, field_name: str = "Signature1"
) -> bytes:
    cms_signer = signers.SimpleSigner(
        signing_cert=signer.asn1(),
        signing_key=signer.asn1_key(),
        cert_registry=SimpleCertificateStore.from_certs([c.asn1() for c in chain]),
    )
    writer = IncrementalPdfFileWriter(io.BytesIO(pdf), strict=False)
    meta = signers.PdfSignatureMetadata(
        field_name=field_name, subfilter=fields.SigSeedSubFilter.PADES
    )
    out = await signers.async_sign_pdf(writer, meta, signer=cms_signer)
    return out.getvalue()


def replace_page_content(signed_pdf: bytes, text: bytes = b"EVIL") -> bytes:
    """An incremental update after signing that swaps the page's content
    stream - what a shadow attack does: the signature still verifies over its
    own revision, but the reader shows something else."""
    writer = IncrementalPdfFileWriter(io.BytesIO(signed_pdf))
    page = writer.root["/Pages"]["/Kids"][0].get_object()
    stream = generic.StreamObject(stream_data=b"BT /F1 24 Tf 10 10 Td (" + text + b") Tj ET")
    page["/Contents"] = writer.add_object(stream)
    writer.update_container(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def flip_signed_byte(signed_pdf: bytes) -> bytes:
    """Changes one byte inside the signed range (the first revision's page
    size), keeping the file length so the byte ranges still line up."""
    assert b"200 200" in signed_pdf
    return signed_pdf.replace(b"200 200", b"200 201", 1)


def cms_sign(
    content: bytes,
    signers: list[Party],
    chain: list[Party],
    *,
    detached: bool,
    pem: bool = False,
) -> bytes:
    """A CMS (CAdES-BES-like) signature: enveloping (`.p7m`) or detached
    (`.p7s`), with one or more parallel signers (P92-S4)."""
    from cryptography.hazmat.primitives.serialization import pkcs7

    builder = pkcs7.PKCS7SignatureBuilder().set_data(content)
    for signer in signers:
        builder = builder.add_signer(signer.cert, signer.key, hashes.SHA256())
    for party in chain:
        builder = builder.add_certificate(party.cert)
    # `Binary`: sign the bytes as they are - without it, cryptography applies
    # S/MIME text canonicalisation (LF -> CRLF) and signs other bytes.
    options = [pkcs7.PKCS7Options.Binary]
    if detached:
        options.append(pkcs7.PKCS7Options.DetachedSignature)
    encoding = serialization.Encoding.PEM if pem else serialization.Encoding.DER
    return builder.sign(encoding, options)
