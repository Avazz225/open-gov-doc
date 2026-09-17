import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12


class DecryptionError(Exception):
    pass


def encrypt(data: bytes, key: bytes) -> bytes:
    """AES-256-GCM (5.2, attribute pseudonymization vault, ADR 0156) - the
    nonce is prepended to the ciphertext bytes, same shape as
    `archival_service.crypto` (ADR 0029, 5.6). Duplicated rather than
    shared - that module's own docstring already contrasts itself with a
    third, differently-shaped crypto module in this project
    (`workflow_service.federation_crypto`, RSA-hybrid), establishing that
    small, single-purpose crypto helpers are deliberately per-service in
    this codebase rather than factored into a shared library."""
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    nonce, ciphertext = data[:_NONCE_LENGTH], data[_NONCE_LENGTH:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise DecryptionError(f"Pseudonymisierter Wert nicht entschlüsselbar: {exc}") from exc
