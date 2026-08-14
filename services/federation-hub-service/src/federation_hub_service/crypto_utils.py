"""Krypto-Bausteine des Hub (ADR 0028, seit P13-S4 auch ADR 0039, seit
Post-Roadmap Phase 21 Session 2 auch ADR 0085): der Hub verschlüsselt/
entschlüsselt selbst **nie** Nutzdaten (die Ende-zu-Ende-Verschlüsselung
findet ausschließlich zwischen den Installationen statt, siehe
`workflow_service.federation_crypto` in der jeweiligen Installation) - er
braucht nur (a) ein eigenes Signaturschlüsselpaar, mit dem er jede Zustellung
an eine Installation signiert, (b) seit P13-S4 die Fähigkeit, eine von einer
Installation mit ihrem eigenen privaten Schlüssel signierte Anfrage zu
verifizieren (ersetzt das bis dahin verwendete API-Key-Hashing - siehe
ADR 0039: "mTLS-äquivalent auf Anwendungsebene" statt eines geteilten
Geheimnisses), und (c) seit Post-Roadmap Phase 21 Session 2 die Fähigkeit,
als eigene kleine interne CA zeitlich begrenzte X.509-Zertifikate für
Installationen auszustellen und zu prüfen (ADR 0085, gleiche Bibliothek/
Konvention wie `signature-service`s interne CA, ADR 0025) - weiterhin
Anwendungsebene, kein echtes Transport-mTLS (siehe ADR 0039s unverändert
gültige Begründung)."""

import base64
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

# Bewusst deutlich kürzer als signature-service's Leaf-Zertifikate (5 Jahre,
# ADR 0025) - dort verhindert eine lange Laufzeit, dass eine später ohne
# Zeitstempeldienst geprüfte PDF-Signatur fälschlich als "abgelaufen" gilt.
# Hier gibt es dieses Problem nicht (Zertifikat und Prüfung passieren beide
# "jetzt", keine Langzeitarchivierung einer Signatur) - eine kürzere Laufzeit
# gibt der Zertifikatsebene stattdessen einen echten, wiederkehrenden
# Erneuerungs-Rhythmus (siehe ADR 0085 "Begründung").
_INSTALLATION_CERTIFICATE_VALIDITY = timedelta(days=365)


def generate_hub_keypair() -> tuple[bytes, bytes]:
    """Erzeugt das Hub-eigene RSA-2048-Schlüsselpaar - wird genau einmal beim
    ersten Start aufgerufen, siehe `repository.get_or_create_hub_identity`
    (Singleton-Muster wie `signature-service`s `get_or_create_ca`). Gibt
    ``(private_key_pem, public_key_pem)`` zurück."""
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
    """Verpackt das bereits vorhandene Hub-Schlüsselpaar als selbstsigniertes
    X.509-Root-CA-Zertifikat (ADR 0085) - KEIN neues Schlüsselpaar, derselbe
    private Schlüssel, der auch `sign_body` unten verwendet. Gleiches Muster
    wie `signature-service.connectors.internal.generate_root_ca`, hier aber
    um ein bereits existierendes Schlüsselpaar herum statt eines neuen."""
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
    """Stellt ein von der Hub-CA signiertes Zertifikat aus, das den
    ÖFFENTLICHEN Schlüssel der Installation bindet - der Hub erzeugt dabei
    KEIN neues Schlüsselpaar (die Installation besitzt und behält ihren
    eigenen privaten Schlüssel, vereinfachtes CSR-Äquivalent ohne
    tatsächliches CSR-Objekt). ``installation_id`` wird als `CommonName`
    eingebettet. Gibt ``(certificate_pem, not_valid_after)`` zurück."""
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
    """Prüft, dass ``certificate_pem`` tatsächlich von der Hub-CA ausgestellt
    wurde (Signaturkette), aktuell innerhalb seiner Gültigkeit liegt, UND
    tatsächlich zu GENAU DIESER Installation gehört (`CommonName` ==
    ``installation_id`` UND eingebetteter öffentlicher Schlüssel ==
    ``installation_public_key_pem``) - ohne die letzten beiden Prüfungen
    könnte ein beliebiges, gültig vom Hub ausgestelltes Zertifikat (z. B. das
    einer anderen Installation) untergeschoben werden, ohne dass die
    Kettenprüfung allein das bemerken würde. Alle vier Prüfungen zusammen
    sind die eigentliche neue Sicherheitseigenschaft dieser Session
    (ADR 0085): ein roher öffentlicher Schlüssel allein hatte weder eine
    geprüfte Herkunft noch eine Gültigkeitsgrenze noch eine gebundene
    Identität."""
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
    """Signiert die rohen Bytes eines an eine Installation zugestellten
    Request-Bodys (RSA-PSS/SHA-256) - die empfangende Installation verifiziert
    mit dem beim Registrieren einmalig abgerufenen öffentlichen Hub-Schlüssel
    (`GET /public-key`), ohne dass irgendwo ein geteiltes Geheimnis im
    Klartext gespeichert werden müsste (siehe ADR 0028)."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(
        body,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def verify_body(public_key_pem: str, body: bytes, signature_b64: str) -> bool:
    """Verifiziert eine von einer Installation mit ihrem eigenen privaten
    Schlüssel signierte eingehende Anfrage (P13-S4, ADR 0039) - Gegenstück zu
    `sign_body` oben, gleiches Schema wie `workflow_service.federation_crypto.
    verify_body`. Der Hub speichert dafür nur den öffentlichen Schlüssel jeder
    Installation (``Installation.public_key_pem``, ohnehin bereits für die
    Ende-zu-Ende-Verschlüsselung vorhanden) - kein zusätzliches Geheimnis."""
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
