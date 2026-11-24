"""Trust anchors for incoming-signature validation (ADR 0248 Decision 3).

Three sources: the internal CA (OG Doc's own signatures), the certificates an
admin uploaded, and - since P92-S2 - the qualified CA services of the EU
trusted lists. Certificates embedded in a validated file may help build a path, but
never become anchors (Decision 6). The OS trust store is never used:
`SimpleTrustManager.build(trust_roots=None)` would load it, so an empty list is
passed explicitly when there are no anchors.
"""

import dataclasses
import hashlib

from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from pyhanko.sign.validation.qualified.tsp import TSPRegistry, TSPTrustManager
from pyhanko_certvalidator.authority import Authority, CertTrustAnchor, TrustAnchor, TrustQualifiers
from pyhanko_certvalidator.registry import SimpleTrustManager, TrustManager

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


EU_SOURCE: TrustSource = "eu_trusted_list"


class CombinedTrustManager(TSPTrustManager):
    """The EU trusted lists plus the installation's own anchors.

    Subclassing `TSPTrustManager` is what makes pyHanko run its qualification
    analysis at all (it checks `isinstance`). EU anchors get the same
    path-length limit as the others."""

    def __init__(
        self, registry: TSPRegistry, simple: SimpleTrustManager, max_path_length: int
    ) -> None:
        super().__init__(registry)
        self._simple = simple
        self._max_path_length = max_path_length

    def _limit(self, anchor: TrustAnchor | None) -> TrustAnchor | None:
        if anchor is None:
            return None
        quals = dataclasses.replace(
            anchor.trust_qualifiers,
            max_path_length=self._max_path_length,
            max_aa_path_length=self._max_path_length,
        )
        return TrustAnchor(anchor.authority, quals=quals)

    def as_trust_anchor(self, authority: Authority) -> TrustAnchor | None:
        own = self._simple.as_trust_anchor(authority)
        if own is not None:
            return own
        return self._limit(super().as_trust_anchor(authority))

    def find_potential_issuers(self, cert):
        yield from self._simple.find_potential_issuers(cert)
        for anchor in super().find_potential_issuers(cert):
            yield self._limit(anchor)


def build_trust_manager(
    anchors: list[TrustAnchorInput],
    *,
    max_path_length: int,
    tsp_registry: TSPRegistry | None = None,
) -> tuple[TrustManager, dict[str, TrustSource]]:
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
    simple = SimpleTrustManager.build(trust_roots=roots)
    if tsp_registry is None:
        return simple, sources
    return CombinedTrustManager(tsp_registry, simple, max_path_length), sources
