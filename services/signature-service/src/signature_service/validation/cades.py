"""CAdES validation (P92-S4, ADR 0248 Decision 2, stage 2).

Two shapes of CMS signatures arrive:

- **Enveloping** (`.p7m`): the signed content is inside. The `.p7m` stays the
  original document; `extract_content` hands the content to the caller, which
  stores it as a derived document so it can be previewed, searched and OCRed.
- **Detached** (`.p7s`): signature only. It is stored as its own document,
  derived from the one it signs (`derivation_type="signature"`), and validated
  against that document's bytes.

Both go through the same AdES process as PAdES: basic validation (§5.3), or
the with-time process when the signer attached a signature timestamp; trust
and qualification come from the same trust manager. Several parallel signers
in one file are validated one by one (pyHanko handles one signer per
SignedData). The CMS may be DER, PEM, or bare base64 (common for `.p7m`).
"""

import asyncio
import base64
import binascii
import logging

from asn1crypto import cms
from cryptography.hazmat.primitives import hashes
from pyhanko.sign.validation import ades, generic_cms
from pyhanko.sign.validation.policy_decl import (
    RevinfoOnlineFetchingRule,
    RevocationInfoGatheringSpec,
    SignatureValidationSpec,
)
from pyhanko_certvalidator.context import CertValidationPolicySpec
from pyhanko_certvalidator.fetchers.aiohttp_fetchers import AIOHttpFetcherBackend
from pyhanko_certvalidator.policy_decl import LEGACY_POLICY_MAP, CertRevTrustPolicy
from pyhanko_certvalidator.util import get_pyca_cryptography_hash

from signature_service.validation.fetcher import build_fenced_session
from signature_service.validation.interface import (
    SignatureFinding,
    ValidationOutcome,
    ValidationPolicy,
)
from signature_service.validation.pades import (
    _indication,
    _name,
    _qualification,
    _reason,
    _trust_source,
)
from signature_service.validation.trust import build_trust_manager

logger = logging.getLogger(__name__)

FORMAT = "cades"
ENVELOPING_TYPES = {"application/pkcs7-mime", "application/x-pkcs7-mime"}
DETACHED_TYPES = {"application/pkcs7-signature", "application/x-pkcs7-signature"}


def is_detached_type(content_type: str | None) -> bool:
    return (content_type or "").split(";")[0].strip().lower() in DETACHED_TYPES


class NotCmsError(ValueError):
    pass


def decode_cms(data: bytes) -> cms.ContentInfo:
    """DER, PEM or bare base64 to a CMS SignedData `ContentInfo`."""
    candidates = [data]
    text = data.strip()
    if text.startswith(b"-----BEGIN"):
        text = b"".join(line for line in text.splitlines() if not line.startswith(b"-----"))
    try:
        candidates.append(base64.b64decode(text, validate=False))
    except (binascii.Error, ValueError):
        pass
    for candidate in candidates:
        try:
            info = cms.ContentInfo.load(candidate)
            if info["content_type"].native == "signed_data":
                # Force parsing now, so a broken structure fails here.
                info["content"]["signer_infos"].native  # noqa: B018
                return info
        except (ValueError, TypeError, KeyError):
            continue
    raise NotCmsError("Keine CMS-Signatur (PKCS#7 SignedData)")


def is_enveloping(info: cms.ContentInfo) -> bool:
    return info["content"]["encap_content_info"]["content"].native is not None


def extract_content(data: bytes) -> bytes | None:
    """The signed content of an enveloping signature, else None. Nested
    enveloping signatures (a `.p7m` inside a `.p7m`) come out one layer at a
    time; the caller stores each as its own derived document."""
    try:
        info = decode_cms(data)
    except NotCmsError:
        return None
    if not is_enveloping(info):
        return None
    content = info["content"]["encap_content_info"]["content"]
    return bytes(content.native) if content.native is not None else None


def _per_signer(signed_data: cms.SignedData) -> list[cms.SignedData]:
    """One SignedData per signer - pyHanko validates exactly one."""
    signer_infos = list(signed_data["signer_infos"])
    if len(signer_infos) <= 1:
        return [signed_data]
    copies = []
    for signer_info in signer_infos:
        copy = signed_data.copy()
        copy["signer_infos"] = cms.SignerInfos([signer_info])
        copies.append(copy)
    return copies


def _has_signature_timestamp(signed_data: cms.SignedData) -> bool:
    signer_info = generic_cms.extract_signer_info(signed_data)
    return next(generic_cms.extract_tst_data_iter(signer_info, signed=False), None) is not None


def _digest(signed_data: cms.SignedData, content: bytes) -> bytes:
    signer_info = generic_cms.extract_signer_info(signed_data)
    algorithm = signer_info["digest_algorithm"]["algorithm"].native
    md = hashes.Hash(get_pyca_cryptography_hash(algorithm))
    md.update(content)
    return md.finalize()


class CadesValidator:
    format = FORMAT

    def applies(self, content_type: str | None, filename: str | None) -> bool:
        base = (content_type or "").split(";")[0].strip().lower()
        if base in ENVELOPING_TYPES | DETACHED_TYPES:
            return True
        name = (filename or "").lower()
        return name.endswith((".p7m", ".p7s"))

    async def validate(self, data: bytes, policy: ValidationPolicy) -> ValidationOutcome:
        """An enveloping signature (a detached one without its content is
        "nicht prüfbar")."""
        return await self._run(data, None, policy, position="enveloping")

    async def validate_detached(
        self, signature: bytes, content: bytes, policy: ValidationPolicy, *, position: str
    ) -> ValidationOutcome:
        return await self._run(signature, content, policy, position=position)

    def _file_finding(self, sub: str, position: str | None, detail: str | None = None):
        return SignatureFinding(
            format=FORMAT,
            position=position,
            kind="file",
            indication="INDETERMINATE",
            sub_indication=sub,
            failure_reason=_reason(sub),
            technical_detail=detail,
        )

    async def _run(
        self, data: bytes, content: bytes | None, policy: ValidationPolicy, *, position: str
    ) -> ValidationOutcome:
        try:
            async with asyncio.timeout(policy.time_limit_seconds):
                return await self._validate(data, content, policy, position)
        except TimeoutError:
            return ValidationOutcome(findings=[self._file_finding("TIME_LIMIT", position)])

    async def _validate(self, data, content, policy, position) -> ValidationOutcome:
        try:
            info = decode_cms(data)
        except NotCmsError as exc:
            return ValidationOutcome(
                findings=[self._file_finding("STRUCTURE_NOT_CHECKABLE", position, str(exc))]
            )
        signed_data = info["content"]
        enveloping = is_enveloping(info)
        if not enveloping and content is None:
            return ValidationOutcome(
                findings=[self._file_finding("SIGNED_DATA_NOT_FOUND", position)]
            )
        signers = _per_signer(signed_data)
        truncated = len(signers) > policy.max_signatures
        signers = signers[: policy.max_signatures]

        trust_manager, sources = build_trust_manager(
            policy.anchors,
            max_path_length=policy.max_path_length,
            tsp_registry=policy.tsp_registry,
        )
        session = build_fenced_session(policy.fetch) if policy.outbound_fetching else None
        try:
            backend = (
                AIOHttpFetcherBackend(
                    session=session, per_request_timeout=policy.fetch.per_request_timeout_seconds
                )
                if session is not None
                else None
            )
            spec = SignatureValidationSpec(
                cert_validation_policy=CertValidationPolicySpec(
                    trust_manager=trust_manager,
                    revinfo_policy=CertRevTrustPolicy(LEGACY_POLICY_MAP[policy.revocation_mode]),
                ),
                revinfo_gathering_policy=RevocationInfoGatheringSpec(
                    online_fetching_rule=(
                        RevinfoOnlineFetchingRule.ALWAYS_FETCH
                        if backend is not None
                        else RevinfoOnlineFetchingRule.LOCAL_ONLY
                    ),
                    **({"fetcher_backend": backend} if backend is not None else {}),
                ),
            )
            findings = []
            for index, one in enumerate(signers):
                label = position if len(signers) == 1 else f"{position}#{index + 1}"
                findings.append(
                    await self._one(
                        one,
                        None if enveloping else content,
                        spec,
                        sources,
                        label,
                        eu_lists=policy.tsp_registry is not None,
                    )
                )
            return ValidationOutcome(findings=findings, truncated=truncated)
        finally:
            if session is not None:
                await session.close()

    async def _one(self, signed_data, content, spec, sources, position, *, eu_lists):
        try:
            raw_digest = _digest(signed_data, content) if content is not None else None
            if _has_signature_timestamp(signed_data):
                result = await ades.ades_with_time_validation(
                    signed_data, spec, raw_digest=raw_digest
                )
                best_time = result.best_signature_time
            else:
                result = await ades.ades_basic_validation(signed_data, spec, raw_digest=raw_digest)
                best_time = None
        except Exception as exc:  # noqa: BLE001 - one signer must not end the run
            logger.info("incoming_cades_validation_error", exc_info=True)
            return SignatureFinding(
                format=FORMAT,
                position=position,
                kind="signature",
                indication="INDETERMINATE",
                sub_indication="VALIDATION_ERROR",
                failure_reason=_reason("VALIDATION_ERROR"),
                technical_detail=f"{type(exc).__name__}: {exc}"[:2000],
            )
        status = result.api_status
        indication = _indication(result.ades_subindic.status.name)
        sub = result.ades_subindic.name
        intact = bool(getattr(status, "intact", False))
        passed = indication == "TOTAL-PASSED"
        qualification, qc_type = _qualification(status, passed)
        signer = getattr(status, "signing_cert", None)
        return SignatureFinding(
            format=FORMAT,
            position=position,
            kind="signature",
            indication=indication,
            sub_indication=sub,
            qualification=qualification,
            qc_type=qc_type,
            trust_source=_trust_source(status, sources, eu_lists=eu_lists) if passed else None,
            signer_subject=_name(signer) if signer is not None else None,
            signer_issuer=signer.issuer.human_friendly if signer is not None else None,
            signer_serial=str(signer.serial_number) if signer is not None else None,
            claimed_signing_time=getattr(status, "signer_reported_dt", None),
            best_signature_time=best_time,
            # A CMS signature has no incremental updates: it covers its
            # content exactly, or the content doesn't match.
            coverage="entire_document" if intact else "changed_after_signing",
            failure_reason=_reason(sub) if not passed else None,
            technical_detail=(result.failure_msg or None) if not passed else None,
        )
