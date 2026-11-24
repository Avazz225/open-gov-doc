"""Validating signatures on incoming document versions (P92-S1, ADR 0248).

`IncomingSignatureService` ties the parts together: it turns a document event
into a job, builds the policy snapshot from the database (anchors, mode,
fetching) and the settings (bounds), runs the matching validator in the
worker, stores the run and publishes `signature.incoming.validated`.
"""

import logging
from collections.abc import Awaitable, Callable

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from dms_eventbus_client import Event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from signature_service import incoming_repository
from signature_service.document_client import DocumentServiceClient
from signature_service.document_client import NotFoundError as DocumentNotFoundError
from signature_service.settings import Settings
from signature_service.validation.interface import (
    FetchLimits,
    SignatureFinding,
    SignatureValidator,
    TrustAnchorInput,
    ValidationOutcome,
    ValidationPolicy,
)
from signature_service.validation.pades import PadesValidator
from signature_service.validation.worker import ValidationJob, ValidationWorker

logger = logging.getLogger(__name__)

HANDLED_EVENTS = {"document.created", "document.version.created"}

PublishEvent = Callable[..., Awaitable[None]]


class IncomingSignatureService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        document_client: DocumentServiceClient,
        internal_ca_pem: bytes,
        publish_event: PublishEvent,
        validators: list[SignatureValidator] | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._documents = document_client
        self._internal_ca_der = x509.load_pem_x509_certificate(internal_ca_pem).public_bytes(
            serialization.Encoding.DER
        )
        self._publish = publish_event
        self.validators: list[SignatureValidator] = validators or [PadesValidator()]
        self.worker = ValidationWorker(
            queue_size=settings.incoming_validation_queue_size,
            workers=settings.incoming_validation_workers,
            process=self.process,
        )

    def validator_for(
        self, content_type: str | None, filename: str | None
    ) -> SignatureValidator | None:
        return next((v for v in self.validators if v.applies(content_type, filename)), None)

    async def handle_event(self, payload: bytes) -> None:
        """Consumer callback for `document.>`: queues versions a validator
        applies to. Events without `version_number` (before P92-S1) are
        skipped."""
        event = Event.from_bytes(payload)
        if event.event_type not in HANDLED_EVENTS:
            return
        data = event.payload or {}
        version_number = data.get("version_number")
        if not isinstance(version_number, int) or version_number < 1:
            return
        content_type, filename = data.get("content_type"), data.get("filename")
        if self.validator_for(content_type, filename) is None:
            return
        await self.worker.submit(
            ValidationJob(
                document_id=event.subject,
                version_number=version_number,
                content_type=content_type,
                filename=filename,
                trigger="event",
            )
        )

    async def _policy(self, session: AsyncSession) -> tuple[bool, ValidationPolicy]:
        config = await incoming_repository.get_config(session)
        anchors = [TrustAnchorInput(self._internal_ca_der, "internal")] + [
            TrustAnchorInput(a.certificate_der, "installation")
            for a in await incoming_repository.list_anchors(session)
        ]
        s = self._settings
        policy = ValidationPolicy(
            anchors=anchors,
            revocation_mode=config.revocation_mode,  # type: ignore[arg-type]
            outbound_fetching=config.outbound_fetching,
            max_signatures=s.incoming_validation_max_signatures,
            max_path_length=s.incoming_validation_max_path_length,
            time_limit_seconds=s.incoming_validation_time_limit_seconds,
            fetch=FetchLimits(
                per_request_timeout_seconds=s.incoming_validation_fetch_timeout_seconds,
                max_response_bytes=s.incoming_validation_fetch_max_bytes,
                proxy=s.signature_validation_http_proxy,
                extra_blocked_networks=list(s.incoming_validation_blocked_networks),
            ),
        )
        await session.commit()
        return config.enabled, policy

    async def process(self, job: ValidationJob) -> None:
        validator = self.validator_for(job.content_type, job.filename)
        if validator is None:
            return
        async with self._session_factory() as session:
            enabled, policy = await self._policy(session)
        if not enabled:
            return
        try:
            meta = await self._documents.get_version_meta(job.document_id, job.version_number)
            if meta["size_bytes"] > self._settings.incoming_validation_max_bytes:
                outcome = ValidationOutcome(
                    findings=[
                        SignatureFinding(
                            format=validator.format,
                            position=None,
                            kind="file",
                            indication="INDETERMINATE",
                            sub_indication="FILE_TOO_LARGE",
                            failure_reason="Datei zu groß für die Signaturprüfung",
                            technical_detail=f"{meta['size_bytes']} Bytes",
                        )
                    ]
                )
            else:
                _content_type, data = await self._documents.get_version_content(
                    job.document_id, job.version_number
                )
                # Cheap pre-check: a signature dictionary can't sit in a
                # compressed object stream (its byte range must be addressable
                # in the file), so a PDF without "/ByteRange" is unsigned.
                if b"/ByteRange" not in data:
                    return
                outcome = await self.worker.run_validator(validator, data, policy)
        except DocumentNotFoundError:
            logger.info("incoming_signature_version_gone", extra={"document_id": job.document_id})
            return
        except httpx.HTTPError as exc:
            # document-service unreachable: the job is dropped (the event was
            # already acknowledged). The admin backfill (P92-S2) catches up.
            logger.warning(
                "incoming_signature_fetch_failed",
                extra={"document_id": job.document_id, "error": type(exc).__name__},
            )
            return
        async with self._session_factory() as session:
            summary = await incoming_repository.record_run(
                session,
                document_id=job.document_id,
                version_number=job.version_number,
                trigger=job.trigger,
                revocation_mode=policy.revocation_mode,
                outcome=outcome,
            )
            await session.commit()
        if summary is None:
            return
        await self._publish(
            "signature.incoming.validated",
            subject=job.document_id,
            payload={
                "version_number": job.version_number,
                "run_id": summary.run_id,
                "signature_count": summary.signature_count,
                "worst_indication": summary.worst_indication,
                "best_qualification": summary.best_qualification,
                "changed_after_signing": summary.changed_after_signing,
                "trigger": job.trigger,
            },
            actor="system:signature-validation",
        )
