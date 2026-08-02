"""Ende-zu-Ende-Verschlüsselung zwischen Installationen (7.4, P6-S9, ADR 0028) -
der Federation Hub selbst besitzt nie einen privaten Schlüssel und sieht daher
nie Klartext, siehe `docs/services/federation-hub-service.md`. Gleiche
Bibliothek/Serialisierung wie `signature-service`s interne CA (RSA-2048,
PEM/PKCS8, `cryptography`) - hier zusätzlich echtes Verschlüsseln/Signaturprüfen
statt nur Signieren.

Envelope-Schema (hybrid, wie TLS/PGP): ein frischer AES-256-GCM-Schlüssel
verschlüsselt die eigentliche JSON-Payload, RSA-OAEP verschlüsselt wiederum
diesen AES-Schlüssel mit dem öffentlichen RSA-Schlüssel der Zielinstallation -
nur deren privater Schlüssel kann ihn wieder freilegen. Das gesamte Envelope
(``encrypted_key``/``nonce``/``ciphertext``, je base64) wird selbst nochmal
base64-kodiert als einzelner opaker String übertragen (`encrypted_payload` in
`federation_hub_service.schemas.HandoverCreate`)."""

import base64
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_OAEP_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None
)


class DecryptionError(Exception):
    """Envelope nicht entschlüsselbar (falscher privater Schlüssel, manipulierte
    Bytes, oder kein gültiges Envelope-JSON)."""


def generate_keypair() -> tuple[bytes, bytes]:
    """Gibt ``(private_key_pem, public_key_pem)`` zurück - wird genau einmal
    beim ersten Start mit konfiguriertem Federation Hub aufgerufen, siehe
    `main.py._ensure_federation_identity` (Singleton-Muster wie
    `signature-service.connectors.internal.generate_root_ca`)."""
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


def encrypt_for(public_key_pem: bytes, payload: dict) -> str:
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, json.dumps(payload).encode("utf-8"), None)

    public_key = serialization.load_pem_public_key(public_key_pem)
    encrypted_key = public_key.encrypt(aes_key, _OAEP_PADDING)

    envelope = {
        "encrypted_key": base64.b64encode(encrypted_key).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")


def decrypt_with(private_key_pem: bytes, encrypted_payload: str) -> dict:
    try:
        envelope = json.loads(base64.b64decode(encrypted_payload))
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        aes_key = private_key.decrypt(base64.b64decode(envelope["encrypted_key"]), _OAEP_PADDING)
        plaintext = AESGCM(aes_key).decrypt(
            base64.b64decode(envelope["nonce"]), base64.b64decode(envelope["ciphertext"]), None
        )
        return json.loads(plaintext)
    except Exception as exc:  # ungültiges Envelope-JSON, falscher Schlüssel, manipulierte Bytes
        raise DecryptionError(f"Payload nicht entschlüsselbar: {exc}") from exc


def sign_body(private_key_pem: bytes, body: bytes) -> str:
    """Signiert einen ausgehenden Request-Body (RSA-PSS/SHA-256) - genutzt für
    eigene Aufrufe gegen den Hub, gleiches Schema wie
    `federation_hub_service.crypto_utils.sign_body`."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(
        body,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def verify_body(public_key_pem: bytes, body: bytes, signature_b64: str) -> bool:
    """Verifiziert eine vom Hub signierte Zustellung (`X-Federation-Hub-
    Signature`) gegen den beim Registrieren einmalig abgerufenen öffentlichen
    Hub-Schlüssel (Trust-on-First-Use, siehe `models.FederationIdentity`)."""
    try:
        public_key = serialization.load_pem_public_key(public_key_pem)
        public_key.verify(
            base64.b64decode(signature_b64),
            body,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False
