import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12


class DecryptionError(Exception):
    pass


def encrypt(data: bytes, key: bytes) -> bytes:
    """AES-256-GCM (5.6, ADR 0029) - der Nonce wird den Ciphertext-Bytes
    vorangestellt (kein separates Envelope-Format noetig, anders als die
    RSA-hybride Installationsuebergreifende Verschluesselung in
    `workflow_service.federation_crypto`: hier gibt es nur einen einzigen,
    symmetrischen Schluessel aus dem `KeyStore`)."""
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    nonce, ciphertext = data[:_NONCE_LENGTH], data[_NONCE_LENGTH:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise DecryptionError(f"Archiv-Inhalt nicht entschlüsselbar: {exc}") from exc
