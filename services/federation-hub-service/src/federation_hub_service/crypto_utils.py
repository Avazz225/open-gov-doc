"""Crypto building blocks of the hub (ADR 0028, since P13-S4 also ADR 0039,
since Post-Roadmap Phase 21 Session 2 also ADR 0085): the hub itself
**never** encrypts/decrypts payload data (end-to-end encryption happens
exclusively between installations, see `workflow_service.federation_crypto`
in the respective installation) - it only needs (a) its own signing key pair,
with which it signs every delivery to an installation, (b) since P13-S4 the
ability to verify a request signed by an installation with its own private
key (replaces the API-key hashing used up to that point - see ADR 0039:
"mTLS-equivalent at the application level" instead of a shared secret), and
(c) since Post-Roadmap Phase 21 Session 2 the ability to act as its own small
internal CA, issuing and checking time-limited X.509 certificates for
installations (ADR 0085, same library/convention as `signature-service`'s
internal CA, ADR 0025) - still application level, no real transport mTLS
(see ADR 0039's still-valid reasoning)."""

import base64
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

# Deliberately much shorter than signature-service's leaf certificates
# (5 years, ADR 0025) - there, a long validity period prevents a PDF
# signature that is later checked without a timestamping service from being
# incorrectly considered "expired". That problem doesn't exist here
# (certificate issuance and verification both happen "now", no long-term
# archival of a signature) - a shorter validity period instead gives the
# certificate layer a real, recurring renewal cadence (see ADR 0085
# "Rationale").
_INSTALLATION_CERTIFICATE_VALIDITY = timedelta(days=365)


def generate_hub_keypair() -> tuple[bytes, bytes]:
    """Generates the hub's own RSA-2048 key pair - called exactly once on the
    first startup, see `repository.get_or_create_hub_identity` (singleton
    pattern like `signature-service`'s `get_or_create_ca`). Returns
    ``(private_key_pem, public_key_pem)``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def generate_ca_certificate(private_key_pem: bytes, public_key_pem: bytes) -> bytes:
    """Wraps the already-existing hub key pair as a self-signed X.509 root CA
    certificate (ADR 0085) - NOT a new key pair, the same private key also
    used by `sign_body` below. Same pattern as
    `signature-service.connectors.internal.generate_root_ca`, but here built
    around an already-existing key pair instead of a new one."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    public_key = serialization.load_pem_public_key(public_key_pem)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "DMS Federation Hub CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DMS"),
        ]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=20 * 365))
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
        .sign(private_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def issue_installation_certificate(
    ca_certificate_pem: bytes,
    ca_private_key_pem: bytes,
    *,
    installation_id: str,
    installation_public_key_pem: str,
) -> tuple[str, datetime]:
    """Issues a certificate signed by the hub CA that binds the installation's
    PUBLIC key - the hub does NOT generate a new key pair here (the
    installation owns and keeps its own private key; a simplified CSR
    equivalent without an actual CSR object). ``installation_id`` is embedded
    as the `CommonName`. Returns ``(certificate_pem, not_valid_after)``."""
    ca_cert = x509.load_pem_x509_certificate(ca_certificate_pem)
    ca_private_key = serialization.load_pem_private_key(ca_private_key_pem, password=None)
    installation_public_key = serialization.load_pem_public_key(
        installation_public_key_pem.encode("utf-8")
    )

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, installation_id)])
    now = datetime.now(UTC)
    not_valid_after = now + _INSTALLATION_CERTIFICATE_VALIDITY
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(installation_public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(not_valid_after)
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
        .sign(ca_private_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii"), not_valid_after


def verify_installation_certificate(
    ca_certificate_pem: bytes,
    certificate_pem: str,
    *,
    installation_id: str,
    installation_public_key_pem: str,
) -> bool:
    """Checks that ``certificate_pem`` was actually issued by the hub CA
    (signature chain), is currently within its validity period, AND actually
    belongs to EXACTLY THIS installation (`CommonName` == ``installation_id``
    AND embedded public key == ``installation_public_key_pem``) - without
    these last two checks, any certificate validly issued by the hub (e.g.
    that of a different installation) could be substituted in without the
    chain check alone noticing. All four checks together are the actual new
    security property of this session (ADR 0085): a raw public key alone had
    neither a verified origin, nor a validity boundary, nor a bound
    identity."""
    try:
        ca_cert = x509.load_pem_x509_certificate(ca_certificate_pem)
        certificate = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
        certificate.verify_directly_issued_by(ca_cert)
        common_name = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        if common_name != installation_id:
            return False
        expected_public_key = serialization.load_pem_public_key(
            installation_public_key_pem.encode("utf-8")
        )
        if certificate.public_key().public_numbers() != expected_public_key.public_numbers():
            return False
    except Exception:
        return False
    now = datetime.now(UTC)
    return certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc


def sign_body(private_key_pem: bytes, body: bytes) -> str:
    """Signs the raw bytes of a request body delivered to an installation
    (RSA-PSS/SHA-256) - the receiving installation verifies it using the
    hub's public key, fetched once during registration (`GET /public-key`),
    without needing to store a shared secret in plaintext anywhere (see
    ADR 0028)."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(
        body,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def verify_body(public_key_pem: str, body: bytes, signature_b64: str) -> bool:
    """Verifies an incoming request signed by an installation with its own
    private key (P13-S4, ADR 0039) - counterpart to `sign_body` above, same
    scheme as `workflow_service.federation_crypto.verify_body`. For this, the
    hub only stores each installation's public key (``Installation.
    public_key_pem``, already present anyway for end-to-end encryption) - no
    additional secret."""
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        public_key.verify(
            base64.b64decode(signature_b64),
            body,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False
