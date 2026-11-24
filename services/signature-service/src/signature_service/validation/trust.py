"""Trust anchors for incoming-signature validation (ADR 0248 Decision 3).

Anchors come only from the installation: the internal CA (OG Doc's own
signatures) and the certificates an admin uploaded. EU trusted lists follow in
P92-S2. Certificates embedded in a validated file may help build a path, but
never become anchors (Decision 6). The OS trust store is never used:
`SimpleTrustManager.build(trust_roots=None)` would load it, so an empty list is
passed explicitly when there are no anchors.
"""

import hashlib

from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from pyhanko_certvalidator.authority import CertTrustAnchor, TrustQualifiers
from pyhanko_certvalidator.registry import SimpleTrustManager

from signature_service.validation.interface import TrustAnchorInput, TrustSource


class InvalidAnchorError(ValueError):
    pass


def fingerprint_sha256(certificate_der: bytes) -> str:
    return hashlib.sha256(certificate_der).hexdigest()


def parse_anchor_pem(pem: bytes) -> x509.Certificate:
    """An uploaded anchor must be one CA certificate."""
    try:
        cert = x509.load_pem_x509_certificate(pem)
    except ValueError as exc:
        raise InvalidAnchorError("Keine gültige PEM-Zertifikatsdatei") from exc
    try:
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound as exc:
        raise InvalidAnchorError(
            "Zertifikat ist kein CA-Zertifikat (BasicConstraints fehlt)"
        ) from exc
    if not constraints.ca:
        raise InvalidAnchorError("Zertifikat ist kein CA-Zertifikat")
    return cert


def der_of(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.DER)


def build_trust_manager(
    anchors: list[TrustAnchorInput], *, max_path_length: int
) -> tuple[SimpleTrustManager, dict[str, TrustSource]]:
    """The trust manager for one run, and a map from anchor fingerprint to
    its source, to label the result."""
    roots = []
    sources: dict[str, TrustSource] = {}
    for anchor in anchors:
        cert = asn1_x509.Certificate.load(anchor.certificate_der)
        # Both limits set on purpose: pyhanko-certvalidator 0.31 copies
        # `max_aa_path_length` only when `max_path_length` is set (a typo in
        # `init_pkix_validation_state`), and a `None` there crashes path
        # validation through an intermediate.
        quals = TrustQualifiers(max_path_length=max_path_length, max_aa_path_length=max_path_length)
        roots.append(CertTrustAnchor(cert, quals=quals))
        # The internal CA wins if the same certificate is also uploaded.
        fp = fingerprint_sha256(anchor.certificate_der)
        if sources.get(fp) != "internal":
            sources[fp] = anchor.source
    return SimpleTrustManager.build(trust_roots=roots), sources
