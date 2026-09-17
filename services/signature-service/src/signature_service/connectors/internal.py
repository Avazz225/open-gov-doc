import io
import tempfile
from datetime import UTC, datetime, timedelta

import asn1crypto.keys as asn1_keys
import asn1crypto.x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import signers
from pyhanko.sign.fields import SigSeedSubFilter
from pyhanko.sign.signers.pdf_signer import PdfTimeStamper
from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
from pyhanko.sign.validation import async_validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore

from signature_service.connectors.interface import (
    SignatureProviderConnector,
    SignedResult,
    SignerInfo,
    VerificationResult,
)

_ROOT_CA_VALIDITY = timedelta(days=20 * 365)
# Deliberately multi-year, not short-lived: PAdES-B-LTA (3.10, Post-Roadmap
# Phase 41 Session 1) makes long-term verifiability the whole point, so a
# short-lived leaf certificate would falsely flag old-but-valid signatures
# as "expired" the moment their leaf cert's own validity window passes -
# the periodic archive-timestamp-chain extension (`extend_timestamp_chain`)
# is what actually keeps a signature verifiable past that point, but the
# leaf's own window is kept generous regardless as a first line of
# defense.
_LEAF_VALIDITY = timedelta(days=5 * 365)
# Reused unchanged for the whole installation lifetime (singleton, like the
# root CA itself) - not reissued per signing operation, so no need to be
# short-lived either.
_TSA_VALIDITY = timedelta(days=10 * 365)
# Regenerated fresh on every embed (see `_build_crl_der`) - an always-empty
# CRL has no real "update cycle", `next_update` only has to be far enough
# out that no verifier flags it as stale before the next re-embed.
_CRL_VALIDITY = timedelta(days=30)


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


def issue_tsa_certificate(
    ca_certificate_pem: bytes, ca_private_key_pem: bytes
) -> tuple[bytes, bytes]:
    """Issues the single, reused internal TSA certificate (RFC 3161) from
    the internal root CA - called exactly once, see
    `repository.get_or_create_tsa` (same singleton pattern as
    `get_or_create_ca`). The Extended Key Usage extension MUST be critical
    and contain *only* `id-kp-timeStamping` (RFC 3161 §2.3) - a generic
    leaf certificate (like `_issue_leaf_certificate`'s signing certs) would
    be rejected by any RFC-3161-conformant validator as a timestamping
    certificate. Returns `(certificate_pem, private_key_pem)`."""
    ca_cert = x509.load_pem_x509_certificate(ca_certificate_pem)
    ca_key = serialization.load_pem_private_key(ca_private_key_pem, password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "DMS Interne Zeitstempel-Instanz (TSA)")]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + _TSA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
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
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _asn1_certificate(certificate_pem: bytes) -> asn1_x509.Certificate:
    der = x509.load_pem_x509_certificate(certificate_pem).public_bytes(serialization.Encoding.DER)
    return asn1_x509.Certificate.load(der)


def _asn1_private_key(private_key_pem: bytes) -> asn1_keys.PrivateKeyInfo:
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return asn1_keys.PrivateKeyInfo.load(der)


def _build_crl_der(ca_certificate_pem: bytes, ca_private_key_pem: bytes) -> bytes:
    """Builds a freshly signed, always-empty CRL (DER) for the internal CA
    - PAdES-B-LT's embedded validation info (3.10) structurally requires
    revocation info alongside the certificate chain, even though nothing in
    this self-signed-CA design is ever meaningfully "revoked" (no
    revocation infrastructure exists, or is planned, for this internal CA -
    see docs/services/signature-service.md "Open Points"). Regenerated on
    every embed (initial `sign()` and later `extend_timestamp_chain()`
    calls) instead of cached, so it is never stale at the moment it's
    embedded."""
    ca_cert = x509.load_pem_x509_certificate(ca_certificate_pem)
    ca_key = serialization.load_pem_private_key(ca_private_key_pem, password=None)
    now = datetime.now(UTC)
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now)
        .next_update(now + _CRL_VALIDITY)
        .sign(ca_key, hashes.SHA256())
    )
    return crl.public_bytes(serialization.Encoding.DER)


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
    signing operation and embeds it into the PDF bytes via **pyHanko**, at
    the PAdES-B-LTA profile (long-term archival, Post-Roadmap Phase 41
    Session 1, ADR 0155): every signature is created with an embedded RFC
    3161 timestamp (`timestamper`) plus validation info (CA chain + CRL,
    `embed_validation_info`/`validation_context`) and `use_pades_lta=True`
    - B-B/B-T/B-LT are not separately offered, since 3.10 does not call for
    a per-signature profile choice and the internal TSA/CRL are already
    self-contained (no cost/latency reason to offer a cheaper profile).
    `level="qes"` is already forbidden in `SignatureProviderConfig` (no
    accredited QTSP available)."""

    def __init__(
        self,
        ca_certificate_pem: bytes,
        ca_private_key_pem: bytes,
        tsa_certificate_pem: bytes,
        tsa_private_key_pem: bytes,
    ) -> None:
        self._ca_certificate_pem = ca_certificate_pem
        self._ca_private_key_pem = ca_private_key_pem
        self._tsa_certificate_pem = tsa_certificate_pem
        self._tsa_private_key_pem = tsa_private_key_pem

    def _build_timestamper(self) -> DummyTimeStamper:
        # `certs_to_embed`: the TSA's own cert chains up to the same
        # internal root CA as the signing leaf cert - embedding it lets a
        # validator resolve the timestamp token's signer without a
        # separate lookup.
        return DummyTimeStamper(
            tsa_cert=_asn1_certificate(self._tsa_certificate_pem),
            tsa_key=_asn1_private_key(self._tsa_private_key_pem),
            certs_to_embed=SimpleCertificateStore.from_certs(
                [_asn1_certificate(self._ca_certificate_pem)]
            ),
        )

    def _build_validation_context(self) -> ValidationContext:
        return ValidationContext(
            trust_roots=[_asn1_certificate(self._ca_certificate_pem)],
            crls=[_build_crl_der(self._ca_certificate_pem, self._ca_private_key_pem)],
            allow_fetching=False,
            revocation_mode="soft-fail",
        )

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
                field_name="DMSSignature",
                reason="Elektronische Signatur (DMS, 3.10)",
                # `subfilter=PADES`: without this, pyHanko defaults to
                # `ADOBE_PKCS7_DETACHED` (plain PKCS#7) - not a real,
                # ETSI-PAdES-conformant signature despite what this
                # connector's name/docstring previously claimed.
                subfilter=SigSeedSubFilter.PADES,
                embed_validation_info=True,
                validation_context=self._build_validation_context(),
                use_pades_lta=True,
            )
            # `signers.sign_pdf` internally calls `asyncio.run(...)` and
            # therefore collides with the already-running event loop of the
            # FastAPI handler - `async_sign_pdf` is the direct,
            # coroutine-native equivalent.
            signed_buffer = await signers.async_sign_pdf(
                writer, meta, signer=cms_signer, timestamper=self._build_timestamper()
            )

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

    async def extend_timestamp_chain(self, pdf_bytes: bytes) -> bytes:
        """Periodic archive-timestamp-chain extension (PAdES-B-LTA, 3.10,
        Post-Roadmap Phase 41 Session 1) - the initial `sign()` call
        already embeds the first archive timestamp (`use_pades_lta=True`),
        so a signature is B-LTA-conformant from the moment it's created;
        this is what keeps that chain *continuous* over the years, ahead
        of the current archive timestamp's own eventual algorithm/cert
        weakening (the actual meaning of "long-term"). Called from
        `main.py`'s periodic poll loop - not exposed as a manual HTTP
        endpoint, same "no manual trigger" precedent as
        `document-service`'s retention poll loop. Not part of
        `SignatureProviderConnector`'s abstract interface - a future,
        externally-hosted connector type (e.g. a real QTSP) may not expose
        (or need) this at all, so callers probe for it with `hasattr`
        instead of relying on it being universally present."""
        reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
        pdf_timestamper = PdfTimeStamper(self._build_timestamper())
        output = await pdf_timestamper.async_update_archival_timestamp_chain(
            reader, self._build_validation_context(), in_place=False
        )
        return output.getvalue()
