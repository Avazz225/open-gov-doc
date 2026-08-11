"""Signatur-Primitive für die optionale föderierte Kontaktsuche (2.5/7.4,
P15-S4) - identische Bibliothek/Serialisierung wie `workflow_service.
federation_crypto` (RSA-2048, PEM/PKCS8, RSA-PSS/SHA-256), hier bewusst
erneut dupliziert statt in eine gemeinsame Lib extrahiert (kein
`libs/`-Paket kannte diese Primitive bisher, sie waren bereits zuvor zweimal
unabhängig in `workflow-service`/`federation-hub-service` dupliziert - dritte
Duplikation folgt demselben, bereits etablierten Präzedenzfall dieses
Projekts statt eine neue Abstraktion für einen einzelnen zusätzlichen
Aufrufer einzuführen). Anders als `workflow_service.federation_crypto` OHNE
Ende-zu-Ende-Verschlüsselung (`encrypt_for`/`decrypt_with`): die föderierte
Kontaktsuche läuft direkt Installation-zu-Installation (nicht über den Hub
relayt, siehe ADR 0054), der Hub sieht die Anfrage/Antwort ohnehin nie - der
Bedrohungsfall, den die Verschlüsselung im Handover-Schema adressiert
("Hub soll nie Klartext sehen"), besteht hier gar nicht."""

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
