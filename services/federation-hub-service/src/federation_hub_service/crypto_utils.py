"""Krypto-Bausteine des Hub (ADR 0028): der Hub verschlüsselt/entschlüsselt
selbst **nie** Nutzdaten (die Ende-zu-Ende-Verschlüsselung findet ausschließlich
zwischen den Installationen statt, siehe `workflow_service.federation_crypto`
in der jeweiligen Installation) - er braucht nur (a) ein eigenes Signatur-
Schlüsselpaar, mit dem er jede Zustellung an eine Installation signiert, und
(b) API-Key-Hashing zur Authentifizierung eingehender Installations-Aufrufe.
Gleiche Bibliothek/Serialisierung wie `signature-service`s interne CA
(RSA-2048, PEM/PKCS8, `cryptography`)."""

import base64
import hashlib
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


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


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    """Schnelles SHA-256 statt eines langsamen Passwort-Hashes (bcrypt/argon2)
    - der API-Key ist bereits ein hochentropischer Zufallswert (32 Bytes über
    `secrets.token_urlsafe`), kein von Menschen gewähltes, erratbares
    Passwort. Ein schneller Hash reicht hier aus, um einen reinen
    DB-Auszugs-Diebstahl nicht direkt in nutzbare Klartext-Keys zu verwandeln."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
