"""Signature primitives for the optional federated contact directory search
(2.5/7.4, P15-S4) - identical library/serialization as `workflow_service.
federation_crypto` (RSA-2048, PEM/PKCS8, RSA-PSS/SHA-256), deliberately
duplicated here again instead of extracted into a shared lib (no `libs/`
package knew these primitives before now, they had already been
independently duplicated twice in `workflow-service`/`federation-hub-service`
- a third duplication follows this project's already-established precedent
instead of introducing a new abstraction for a single additional caller).
Unlike `workflow_service.federation_crypto`, WITHOUT end-to-end encryption
(`encrypt_for`/`decrypt_with`): the federated contact directory search runs
directly installation-to-installation (not relayed through the Hub, see ADR
0054), the Hub never sees the request/response anyway - the threat that the
encryption in the handover scheme addresses ("Hub should never see
plaintext") simply does not exist here."""

import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def generate_keypair() -> tuple[bytes, bytes]:
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
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(
        body,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def verify_body(public_key_pem: bytes, body: bytes, signature_b64: str) -> bool:
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
