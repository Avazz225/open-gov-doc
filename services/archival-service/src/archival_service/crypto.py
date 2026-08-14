import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12


class DecryptionError(Exception):
    pass


def encrypt(data: bytes, key: bytes) -> bytes:
    """AES-256-GCM (5.6, ADR 0029) - the nonce is prepended to the
    ciphertext bytes (no separate envelope format needed, unlike the
    RSA-hybrid cross-installation encryption in
    `workflow_service.federation_crypto`: here there is only a single
    symmetric key from the `KeyStore`)."""
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    nonce, ciphertext = data[:_NONCE_LENGTH], data[_NONCE_LENGTH:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise DecryptionError(f"Archiv-Inhalt nicht entschlüsselbar: {exc}") from exc
