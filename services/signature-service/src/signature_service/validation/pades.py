"""PAdES validation of incoming PDFs (ADR 0248 Decisions 2, 4 and 6).

Every signature field is checked: approval signatures, certification
signatures (DocMDP) and document timestamps.

- **Strict reading.** OG Doc signs its *own* PDFs leniently (hybrid xref tables
  from LibreOffice), but for a foreign signature that leniency is what shadow
  attacks exploit. A file the strict reader refuses gets one `INDETERMINATE`
  finding, "Dateistruktur nicht prüfbar" - never a lenient second try.
- **AdES per EN 319 102-1.** A signature with a signature timestamp goes
  through the long-term (LTA) process, which also uses the document's DSS and
  document timestamps; one without (PAdES-B-B) through basic validation
  (§5.3), since the with-time process would reject it for the missing
  timestamp alone.
- **Modification analysis.** pyHanko's difference analysis classifies what
  later revisions did. Anything beyond form filling, annotations and
  validation data makes the finding `TOTAL-FAILED` / `MODIFIED_AFTER_SIGNING`,
  whatever the cryptography says: the signed content is no longer what is
  shown.
"""

import asyncio
import io
import logging
from datetime import datetime

from pyhanko.pdf_utils.misc import PdfReadError
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import ades, generic_cms
from pyhanko.sign.validation.errors import SignatureValidationError
from pyhanko.sign.validation.pdf_embedded import (
    EmbeddedPdfSignature,
    async_validate_pdf_timestamp,
)
from pyhanko.sign.validation.policy_decl import (
    PdfSignatureValidationSpec,
    RevinfoOnlineFetchingRule,
    RevocationInfoGatheringSpec,
    SignatureValidationSpec,
)
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.context import CertValidationPolicySpec
from pyhanko_certvalidator.fetchers.aiohttp_fetchers import AIOHttpFetcherBackend
from pyhanko_certvalidator.policy_decl import LEGACY_POLICY_MAP, CertRevTrustPolicy

from signature_service.validation.fetcher import build_fenced_session
from signature_service.validation.interface import (
    Coverage,
    SignatureFinding,
    ValidationOutcome,
    ValidationPolicy,
)
from signature_service.validation.trust import build_trust_manager, fingerprint_sha256

logger = logging.getLogger(__name__)

FORMAT = "pades"

_PERMITTED_LEVELS = {
    ModificationLevel.LTA_UPDATES,
    ModificationLevel.FORM_FILLING,
    ModificationLevel.ANNOTATIONS,
}

# Plain German for the sub-indications users actually meet; anything else
# falls back to a generic sentence plus the technical code.
_REASONS: dict[str, str] = {
    "HASH_FAILURE": "Inhalt wurde nach dem Signieren verändert (Prüfsumme stimmt nicht)",
    "SIG_CRYPTO_FAILURE": "Signaturwert ist mathematisch ungültig",
    "REVOKED": "Zertifikat der unterzeichnenden Person wurde gesperrt",
    "REVOKED_NO_POE": "Zertifikat wurde gesperrt; kein Nachweis, dass vorher signiert wurde",
    "REVOKED_CA_NO_POE": "Ein Zertifikat der Ausstellerkette wurde gesperrt",
    "NO_CERTIFICATE_CHAIN_FOUND": "Aussteller ist nicht als vertrauenswürdig hinterlegt",
    "CERTIFICATE_CHAIN_GENERAL_FAILURE": (
        "Zertifikatskette nicht prüfbar (z. B. Sperrstatus nicht abrufbar)"
    ),
    "OUT_OF_BOUNDS_NO_POE": "Zertifikat war zum Prüfzeitpunkt abgelaufen oder noch nicht gültig",
    "OUT_OF_BOUNDS_NOT_REVOKED": "Zertifikat ist abgelaufen",
    "TRY_LATER": "Sperrstatus derzeit nicht abrufbar - später erneut prüfen",
    "CRYPTO_CONSTRAINTS_FAILURE_NO_POE": "Verwendete Algorithmen gelten als unsicher",
    "CRYPTO_CONSTRAINTS_FAILURE": "Verwendete Algorithmen gelten als unsicher",
    "CHAIN_CONSTRAINTS_FAILURE": "Zertifikatskette verletzt Vorgaben (z. B. zu lang)",
    "SIG_CONSTRAINTS_FAILURE": "Signatur erfüllt die Formvorgaben nicht",
    "FORMAT_FAILURE": "Signaturformat nicht lesbar",
    "SIGNED_DATA_NOT_FOUND": "Signierte Daten nicht auffindbar",
    "MODIFIED_AFTER_SIGNING": "Dokument wurde nach dem Signieren unzulässig verändert",
    "STRUCTURE_NOT_CHECKABLE": "Dateistruktur nicht prüfbar",
    "TIME_LIMIT": "Prüfung hat das Zeitlimit überschritten",
    "VALIDATION_ERROR": "Prüfung nicht möglich",
}


def _reason(sub_indication: str | None) -> str | None:
    if sub_indication is None or sub_indication == "OK":
        return None
    return _REASONS.get(sub_indication, f"Prüfung nicht erfolgreich ({sub_indication})")


def _indication(status_name: str) -> str:
    return {"PASSED": "TOTAL-PASSED", "FAILED": "TOTAL-FAILED"}.get(status_name, "INDETERMINATE")


def _coverage(status) -> tuple[Coverage, str | None]:
    """Coverage and modification level from a pyHanko status object or the
    dict `summarise_integrity_info()` returns."""
    get = status.get if isinstance(status, dict) else (lambda k: getattr(status, k, None))
    level = get("modification_level")
    if level is None:
        # The summary carries the difference analysis itself: a `DiffResult`,
        # or the exception it raised (`SuspiciousModification`) - which means
        # changes the policy can't explain.
        diff = get("diff_result")
        if diff is not None:
            level = getattr(diff, "modification_level", None) or ModificationLevel.OTHER
    coverage_level = get("coverage")
    docmdp_ok = get("docmdp_ok")
    level_name = level.name if level is not None else None
    if coverage_level == SignatureCoverageLevel.ENTIRE_FILE:
        return "entire_document", level_name
    if (
        coverage_level == SignatureCoverageLevel.ENTIRE_REVISION
        and level in _PERMITTED_LEVELS
        and docmdp_ok is not False
    ):
        return "permitted_changes", level_name
    if coverage_level is None and level is None:
        return "unknown", None
    return "changed_after_signing", level_name


def _name(cert) -> str | None:
    try:
        return cert.subject.human_friendly
    except Exception:  # noqa: BLE001 - a broken name must not break the run
        return None


def _trust_source(status, sources: dict[str, str]) -> str | None:
    if not getattr(status, "trusted", False):
        return None
    path = getattr(status, "validation_path", None)
    if path is None:
        return None
    anchor_cert = getattr(path.trust_anchor, "certificate", None)
    if anchor_cert is None:
        return None
    return sources.get(fingerprint_sha256(anchor_cert.dump()))


def _has_signature_timestamp(embedded: EmbeddedPdfSignature) -> bool:
    signer_info = generic_cms.extract_signer_info(embedded.signed_data)
    return next(generic_cms.extract_tst_data_iter(signer_info, signed=False), None) is not None


class PadesValidator:
    format = FORMAT

    def applies(self, content_type: str | None, filename: str | None) -> bool:
        if content_type and content_type.split(";")[0].strip().lower() == "application/pdf":
            return True
        return bool(filename and filename.lower().endswith(".pdf"))

    async def validate(self, data: bytes, policy: ValidationPolicy) -> ValidationOutcome:
        try:
            async with asyncio.timeout(policy.time_limit_seconds):
                return await self._validate(data, policy)
        except TimeoutError:
            return ValidationOutcome(
                findings=[self._file_finding("TIME_LIMIT", f"> {policy.time_limit_seconds}s")]
            )

    def _file_finding(self, sub: str, detail: str | None = None) -> SignatureFinding:
        return SignatureFinding(
            format=FORMAT,
            position=None,
            kind="file",
            indication="INDETERMINATE",
            sub_indication=sub,
            failure_reason=_reason(sub),
            technical_detail=detail,
        )

    async def _validate(self, data: bytes, policy: ValidationPolicy) -> ValidationOutcome:
        try:
            reader = PdfFileReader(io.BytesIO(data), strict=True)
            signatures = list(reader.embedded_signatures)
        except (PdfReadError, ValueError, KeyError, TypeError) as exc:
            return ValidationOutcome(
                findings=[
                    self._file_finding("STRUCTURE_NOT_CHECKABLE", f"{type(exc).__name__}: {exc}")
                ]
            )
        truncated = len(signatures) > policy.max_signatures
        signatures = signatures[: policy.max_signatures]
        if not signatures:
            return ValidationOutcome(findings=[])

        trust_manager, sources = build_trust_manager(
            policy.anchors, max_path_length=policy.max_path_length
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
            gathering = RevocationInfoGatheringSpec(
                online_fetching_rule=(
                    RevinfoOnlineFetchingRule.ALWAYS_FETCH
                    if backend is not None
                    else RevinfoOnlineFetchingRule.LOCAL_ONLY
                ),
                **({"fetcher_backend": backend} if backend is not None else {}),
            )
            cert_policy = CertValidationPolicySpec(
                trust_manager=trust_manager,
                revinfo_policy=CertRevTrustPolicy(LEGACY_POLICY_MAP[policy.revocation_mode]),
            )
            spec = SignatureValidationSpec(
                cert_validation_policy=cert_policy, revinfo_gathering_policy=gathering
            )
            findings = []
            for embedded in signatures:
                findings.append(
                    await self._one(embedded, spec, policy, trust_manager, sources, backend)
                )
            return ValidationOutcome(findings=findings, truncated=truncated)
        finally:
            if session is not None:
                await session.close()

    async def _one(
        self, embedded, spec, policy, trust_manager, sources, backend
    ) -> SignatureFinding:
        position = None
        try:
            position = embedded.field_name
            if embedded.sig_object_type == "/DocTimeStamp":
                return await self._doc_timestamp(
                    embedded, policy, trust_manager, sources, backend, position
                )
            return await self._signature(embedded, spec, sources, position)
        except (SignatureValidationError, PdfReadError, ValueError, KeyError) as exc:
            logger.info("incoming_signature_validation_error", exc_info=True)
            return SignatureFinding(
                format=FORMAT,
                position=position,
                kind="signature",
                indication="INDETERMINATE",
                sub_indication="VALIDATION_ERROR",
                failure_reason=_reason("VALIDATION_ERROR"),
                technical_detail=f"{type(exc).__name__}: {exc}"[:2000],
            )

    async def _signature(self, embedded, spec, sources, position) -> SignatureFinding:
        kind = "certification" if embedded.docmdp_level is not None else "signature"
        if _has_signature_timestamp(embedded):
            result = await ades.ades_lta_validation(embedded, PdfSignatureValidationSpec(spec))
            best_time: datetime | None = result.best_signature_time
        else:
            # Runs the difference analysis (DEFAULT_DIFF_POLICY); the LTA path
            # above does this itself. pyHanko 0.36's basic validation builds a
            # plain CMS status whatever `status_cls` says, so the integrity
            # facts are read from the summary instead of the status.
            embedded.compute_integrity_info()
            result = await ades.ades_basic_validation(
                embedded.signed_data, spec, raw_digest=embedded.compute_digest()
            )
            best_time = None
        status = result.api_status
        indication = _indication(result.ades_subindic.status.name)
        sub = result.ades_subindic.name
        coverage, level_name = _coverage(embedded.summarise_integrity_info())
        if coverage == "changed_after_signing" and indication != "TOTAL-FAILED":
            indication, sub = "TOTAL-FAILED", "MODIFIED_AFTER_SIGNING"
        signer = embedded.signer_cert
        passed = indication == "TOTAL-PASSED"
        trust_source = _trust_source(status, sources) if passed else None
        return SignatureFinding(
            format=FORMAT,
            position=position,
            kind=kind,
            indication=indication,
            sub_indication=sub,
            # "ades" = an advanced signature proven against an installation or
            # internal anchor; "qes"/"adesqc" need the EU trusted lists (S2).
            qualification="ades" if passed else "unknown",
            trust_source=trust_source,
            signer_subject=_name(signer),
            signer_issuer=signer.issuer.human_friendly,
            signer_serial=str(signer.serial_number),
            claimed_signing_time=getattr(status, "signer_reported_dt", None),
            best_signature_time=best_time,
            coverage=coverage,
            modification_level=level_name,
            failure_reason=_reason(sub) if not passed else None,
            technical_detail=(result.failure_msg or None) if not passed else None,
        )

    async def _doc_timestamp(
        self, embedded, policy, trust_manager, sources, backend, position
    ) -> SignatureFinding:
        context = ValidationContext(
            trust_manager=trust_manager,
            allow_fetching=backend is not None,
            fetcher_backend=backend,
            revocation_mode=policy.revocation_mode,
        )
        status = await async_validate_pdf_timestamp(embedded, validation_context=context)
        coverage, level_name = _coverage(status)
        if not status.intact:
            indication, sub = "TOTAL-FAILED", "HASH_FAILURE"
        elif coverage == "changed_after_signing":
            indication, sub = "TOTAL-FAILED", "MODIFIED_AFTER_SIGNING"
        elif not status.trusted:
            indication, sub = "INDETERMINATE", "NO_CERTIFICATE_CHAIN_FOUND"
        else:
            indication, sub = "TOTAL-PASSED", "OK"
        passed = indication == "TOTAL-PASSED"
        tsa = embedded.signer_cert
        return SignatureFinding(
            format=FORMAT,
            position=position,
            kind="document_timestamp",
            indication=indication,
            sub_indication=sub,
            trust_source=_trust_source(status, sources) if passed else None,
            signer_subject=_name(tsa),
            signer_issuer=tsa.issuer.human_friendly,
            signer_serial=str(tsa.serial_number),
            best_signature_time=getattr(status, "timestamp", None),
            coverage=coverage,
            modification_level=level_name,
            failure_reason=_reason(sub) if not passed else None,
        )


__all__ = ["FORMAT", "PadesValidator"]
