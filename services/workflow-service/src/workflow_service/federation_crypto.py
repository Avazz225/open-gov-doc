"""End-to-end encryption between installations (7.4, P6-S9, ADR 0028) -
the Federation Hub itself never holds a private key and therefore never
sees plaintext, see `docs/services/federation-hub-service.md`. Same
library/serialization as `signature-service`'s internal CA (RSA-2048,
PEM/PKCS8, `cryptography`) - here additionally real encryption/signature
verification instead of just signing.

Envelope schema (hybrid, like TLS/PGP): a fresh AES-256-GCM key encrypts
the actual JSON payload, RSA-OAEP in turn encrypts this AES key with the
target installation's public RSA key - only its private key can unlock it
again. The entire envelope (``encrypted_key``/``nonce``/``ciphertext``, each
base64) is itself base64-encoded again and transmitted as a single opaque
string (`encrypted_payload` in `federation_hub_service.schemas.HandoverCreate`)."""

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
    """Envelope cannot be decrypted (wrong private key, tampered bytes, or
    no valid envelope JSON)."""


def generate_keypair() -> tuple[bytes, bytes]:
    """Returns ``(private_key_pem, public_key_pem)`` - called exactly once
    on the first start with a configured Federation Hub, see
    `main.py._ensure_federation_identity` (singleton pattern like
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
    except Exception as exc:  # invalid envelope JSON, wrong key, tampered bytes
        raise DecryptionError(f"Payload nicht entschlüsselbar: {exc}") from exc


def sign_body(private_key_pem: bytes, body: bytes) -> str:
    """Signs an outgoing request body (RSA-PSS/SHA-256) - used for our own
    calls against the Hub, same scheme as
    `federation_hub_service.crypto_utils.sign_body`."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(
        body,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def verify_body(public_key_pem: bytes, body: bytes, signature_b64: str) -> bool:
    """Verifies a delivery signed by the Hub (`X-Federation-Hub-Signature`)
    against the public Hub key retrieved once during registration
    (trust-on-first-use, see `models.FederationIdentity`)."""
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
