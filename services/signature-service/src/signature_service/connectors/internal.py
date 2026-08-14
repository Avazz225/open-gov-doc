import io
import tempfile
from datetime import UTC, datetime, timedelta

import asn1crypto.x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import signers
from pyhanko.sign.validation import async_validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

from signature_service.connectors.interface import (
    SignatureProviderConnector,
    SignedResult,
    SignerInfo,
    VerificationResult,
)

_ROOT_CA_VALIDITY = timedelta(days=20 * 365)
# Deliberately multi-year instead of short-lived: this session implements
# PAdES-B-B (see docs/services/signature-service.md "Open Points") -
# without a timestamp authority and long-term archival profile
# (PAdES-B-LTA, 3.10), a short-lived leaf certificate validity would
# otherwise falsely flag every later verification as "expired", even
# though the actual signing operation itself was completely valid at the
# time of signing.
_LEAF_VALIDITY = timedelta(days=5 * 365)


def generate_root_ca() -> tuple[bytes, bytes]:
    """Generates a new, self-signed internal root CA (RSA 2048, 3.10:
    "system-internal/self-signed keys") - called exactly once on first
    startup, see `repository.get_or_create_ca` (singleton pattern like
    `OcrConfig`). Returns `(certificate_pem, private_key_pem)`."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "DMS Interne Signatur-CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DMS"),
        ]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + _ROOT_CA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _issue_leaf_certificate(
    ca_certificate_pem: bytes,
    ca_private_key_pem: bytes,
    *,
    common_name: str,
    email: str | None,
) -> tuple[bytes, bytes, x509.Certificate]:
    """Issues a new leaf certificate per signing operation, signed by the
    internal root CA - `common_name`/`email` make AES "uniquely
    attributable to a person" (3.10), without requiring an external
    provider (SES instead uses a generic, non-personal `common_name`, see
    `InternalSelfSignedConnector.sign`). Returns `(cert_pem, key_pem,
    cert_object)`."""
    ca_cert = x509.load_pem_x509_certificate(ca_certificate_pem)
    ca_key = serialization.load_pem_private_key(ca_private_key_pem, password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name_attributes = [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    if email:
        name_attributes.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email))
    subject = x509.Name(name_attributes)
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + _LEAF_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem, cert


class InternalSelfSignedConnector(SignatureProviderConnector):
    """The only actually implemented connector for this session (3.10:
    "for this basic scaffold only SES/AES with system-internal/self-signed
    keys") - issues a leaf certificate from the internal root CA per
    signing operation and embeds it into the PDF bytes via **pyHanko**
    (PAdES-B-B). `level="qes"` is already forbidden in
    `SignatureProviderConfig` (no accredited QTSP available)."""

    def __init__(self, ca_certificate_pem: bytes, ca_private_key_pem: bytes) -> None:
        self._ca_certificate_pem = ca_certificate_pem
        self._ca_private_key_pem = ca_private_key_pem

    async def sign(self, pdf_bytes: bytes, *, signer: SignerInfo, level: str) -> SignedResult:
        if level == "aes":
            common_name = signer.display_name
            email = signer.email
        else:
            # SES (Simple Electronic Signature): deliberately no personal
            # certificate - "simple electronic confirmation", see 3.10.
            common_name = "DMS System (SES)"
            email = None

        leaf_cert_pem, leaf_key_pem, leaf_cert = _issue_leaf_certificate(
            self._ca_certificate_pem,
            self._ca_private_key_pem,
            common_name=common_name,
            email=email,
        )

        with (
            tempfile.NamedTemporaryFile(suffix=".pem") as key_file,
            tempfile.NamedTemporaryFile(suffix=".pem") as cert_file,
            tempfile.NamedTemporaryFile(suffix=".pem") as ca_file,
        ):
            key_file.write(leaf_key_pem)
            key_file.flush()
            cert_file.write(leaf_cert_pem)
            cert_file.flush()
            ca_file.write(self._ca_certificate_pem)
            ca_file.flush()

            cms_signer = signers.SimpleSigner.load(
                key_file.name, cert_file.name, ca_chain_files=(ca_file.name,)
            )
            # `strict=False`: many real-world PDF producers (including
            # LibreOffice) write a hybrid cross-reference table (classic
            # xref table + an additional `/XRefStm` for readers prior to
            # PDF 1.5) - pyHanko rejects this in strict mode with a
            # `SigningError`, since a hybrid history could theoretically be
            # abused for "shadow attacks" during later validation. This
            # concerns the document history that already existed before
            # this signature, though, not the incremental change we append
            # ourselves - for a first signature on an uploaded document
            # this is too common a legitimate case to reject outright.
            writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes), strict=False)
            meta = signers.PdfSignatureMetadata(
                field_name="DMSSignature", reason="Elektronische Signatur (DMS, 3.10)"
            )
            # `signers.sign_pdf` internally calls `asyncio.run(...)` and
            # therefore collides with the already-running event loop of the
            # FastAPI handler - `async_sign_pdf` is the direct,
            # coroutine-native equivalent.
            signed_buffer = await signers.async_sign_pdf(writer, meta, signer=cms_signer)

        return SignedResult(
            signed_pdf_bytes=signed_buffer.getvalue(),
            certificate_subject=leaf_cert.subject.rfc4514_string(),
            certificate_serial=str(leaf_cert.serial_number),
            certificate_not_before=leaf_cert.not_valid_before_utc,
            certificate_not_after=leaf_cert.not_valid_after_utc,
        )

    async def verify(self, pdf_bytes: bytes) -> VerificationResult:
        # `strict=False` for the same reason as in `sign()` - a document
        # with a hybrid cross-reference table in its prior history would
        # otherwise no longer be verifiable even after a successful
        # signature.
        reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
        if not reader.embedded_signatures:
            return VerificationResult(
                integrity_intact=False,
                trusted=False,
                errors=["Keine eingebettete Signatur gefunden"],
            )
        embedded_signature = reader.embedded_signatures[0]

        ca_cert = x509.load_pem_x509_certificate(self._ca_certificate_pem)
        ca_der = ca_cert.public_bytes(serialization.Encoding.DER)
        validation_context = ValidationContext(
            trust_roots=[asn1_x509.Certificate.load(ca_der)],
            allow_fetching=False,
            revocation_mode="soft-fail",
        )
        status = await async_validate_pdf_signature(
            embedded_signature, signer_validation_context=validation_context
        )

        errors = []
        if not status.intact:
            errors.append("Inhalt seit der Signatur verändert (Integrität verletzt)")
        if not status.trusted:
            errors.append(
                "Zertifikatskette nicht bis zur internen Root-CA vertrauenswürdig auflösbar"
            )
        return VerificationResult(
            integrity_intact=bool(status.intact), trusted=bool(status.trusted), errors=errors
        )
