import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12


class DecryptionError(Exception):
    pass


def encrypt(data: bytes, key: bytes) -> bytes:
    """AES-256-GCM (5.2, attribute pseudonymization vault, Phase 58 Session
    1, mirrors ADR 0156) - identical shape to `document_service.crypto`/
    `folder_service.crypto` (deliberately duplicated per-service, see
    those modules' own docstrings for the established rationale)."""
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    nonce, ciphertext = data[:_NONCE_LENGTH], data[_NONCE_LENGTH:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise DecryptionError(f"Pseudonymisierter Wert nicht entschlüsselbar: {exc}") from exc
