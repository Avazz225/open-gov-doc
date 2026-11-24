"""Validating signatures on incoming document versions (P92-S1, ADR 0248).

`IncomingSignatureService` ties the parts together: it turns a document event
into a job, builds the policy snapshot from the database (anchors, mode,
fetching) and the settings (bounds), runs the matching validator in the
worker, stores the run and publishes `signature.incoming.validated`.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from dms_eventbus_client import Event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from signature_service import incoming_repository
from signature_service.document_client import DocumentServiceClient
from signature_service.document_client import NotFoundError as DocumentNotFoundError
from signature_service.models import TrustedListFetch
from signature_service.settings import Settings
from signature_service.storage_client import StorageClient
from signature_service.validation import trusted_lists
from signature_service.validation.fetcher import build_fenced_session
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
PDF = "application/pdf"


def report_key(document_id: str, run_id: str) -> str:
    return f"signature-validation-reports/{document_id}/{run_id}.xml"


@dataclass
class BackfillStatus:
    """The latest backfill, kept in memory (lost on restart - a backfill is
    an admin action that can simply be repeated)."""

    state: str = "idle"  # idle | running | done | failed
    started_at: datetime | None = None
    finished_at: datetime | None = None
    started_by: str | None = None
    criteria: dict = field(default_factory=dict)
    scanned: int = 0
    queued: int = 0
    skipped_validated: int = 0
    limit_reached: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


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
        storage: StorageClient | None = None,
        validators: list[SignatureValidator] | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._documents = document_client
        self._internal_ca_der = x509.load_pem_x509_certificate(internal_ca_pem).public_bytes(
            serialization.Encoding.DER
        )
        self._publish = publish_event
        self._storage = storage
        # In-memory registry of the EU trusted lists, rebuilt when the stored
        # set changes (`trusted_list_generation`).
        self._registry: object | None = None
        self._registry_generation: str | None = None
        self._registry_lock = asyncio.Lock()
        self._fetch_lock = asyncio.Lock()
        self.backfill_status = BackfillStatus()
        self._backfill_task: asyncio.Task | None = None
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
        registry = await self._tsp_registry(session) if config.eu_trusted_lists else None
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
            tsp_registry=registry,
        )
        await session.commit()
        return config.enabled, policy

    async def _tsp_registry(self, session: AsyncSession):
        """The registry for the stored national lists, built once per change
        in a thread (parsing every member state's list takes seconds)."""
        generation = await incoming_repository.trusted_list_generation(session)
        if not generation:
            return None
        if generation == self._registry_generation:
            return self._registry
        async with self._registry_lock:
            if generation != self._registry_generation:
                xmls = await incoming_repository.national_list_xmls(session)
                registry, errors = await asyncio.to_thread(trusted_lists.build_registry, xmls)
                if errors:
                    logger.warning("trusted_list_registry_errors", extra={"count": len(errors)})
                self._registry, self._registry_generation = registry, generation
        return self._registry

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
        if not outcome.findings:
            return
        run_id = str(uuid.uuid4())
        stored_report_key = None
        if outcome.report_xml and self._storage is not None:
            key = report_key(job.document_id, run_id)
            try:
                await self._storage.upload(key, outcome.report_xml.encode(), "application/xml")
                stored_report_key = key
            except httpx.HTTPError:
                # The result counts without its report; the run says so.
                logger.warning("incoming_signature_report_store_failed", exc_info=True)
        async with self._session_factory() as session:
            summary = await incoming_repository.record_run(
                session,
                document_id=job.document_id,
                version_number=job.version_number,
                trigger=job.trigger,
                revocation_mode=policy.revocation_mode,
                outcome=outcome,
                run_id=run_id,
                report_key=stored_report_key,
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
                "requested_by": job.requested_by,
                "signers": [
                    {
                        "subject": f.signer_subject,
                        "indication": f.indication,
                        "qualification": f.qualification,
                    }
                    for f in outcome.findings
                    if f.kind in ("signature", "certification")
                ],
            },
            actor=job.requested_by or "system:signature-validation",
        )

    # --- re-validation and backfill -----------------------------------------

    async def revalidate(
        self, document_id: str, version_number: int | None, *, requested_by: str
    ) -> int:
        """Queues one version (default: the current one) again. Returns the
        version number. The run is added; earlier runs stay."""
        if version_number is None:
            document = await self._documents.get_document(document_id)
            version_number = document["current_version_number"]
        meta = await self._documents.get_version_meta(document_id, version_number)
        await self.worker.submit(
            ValidationJob(
                document_id=document_id,
                version_number=version_number,
                content_type=meta.get("content_type"),
                filename=meta.get("filename"),
                trigger="revalidation",
                requested_by=requested_by,
            )
        )
        return version_number

    def backfill_running(self) -> bool:
        return self._backfill_task is not None and not self._backfill_task.done()

    def start_backfill(
        self,
        *,
        folder_id: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        only_unvalidated: bool,
        requested_by: str,
    ) -> BackfillStatus:
        self.backfill_status = BackfillStatus(
            state="running",
            started_at=datetime.now(UTC),
            started_by=requested_by,
            criteria={
                "folder_id": folder_id,
                "created_from": created_from.isoformat() if created_from else None,
                "created_to": created_to.isoformat() if created_to else None,
                "only_unvalidated": only_unvalidated,
            },
        )
        self._backfill_task = asyncio.create_task(
            self._backfill(folder_id, created_from, created_to, only_unvalidated, requested_by)
        )
        return self.backfill_status

    async def _backfill(self, folder_id, created_from, created_to, only_unvalidated, requested_by):
        status = self.backfill_status
        after_id = 0
        try:
            while not status.limit_reached:
                page = await self._documents.list_versions(
                    created_from=created_from,
                    created_to=created_to,
                    folder_id=folder_id,
                    content_type=PDF,
                    after_id=after_id,
                    limit=500,
                )
                if not page:
                    break
                after_id = page[-1]["id"]
                keys = [(v["document_id"], v["version_number"]) for v in page]
                done = set()
                if only_unvalidated:
                    async with self._session_factory() as session:
                        done = await incoming_repository.validated_versions(session, keys)
                for version in page:
                    if status.scanned >= self._settings.incoming_backfill_max_versions:
                        status.limit_reached = True
                        break
                    status.scanned += 1
                    key = (version["document_id"], version["version_number"])
                    if key in done:
                        status.skipped_validated += 1
                        continue
                    await self.worker.submit(
                        ValidationJob(
                            document_id=version["document_id"],
                            version_number=version["version_number"],
                            content_type=version.get("content_type"),
                            filename=version.get("filename"),
                            trigger="backfill",
                            requested_by=requested_by,
                        )
                    )
                    status.queued += 1
            status.state = "done"
        except Exception as exc:  # noqa: BLE001 - reported on the status
            logger.exception("incoming_signature_backfill_failed")
            status.state, status.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        finally:
            status.finished_at = datetime.now(UTC)

    async def stop_backfill(self) -> None:
        if self.backfill_running():
            self._backfill_task.cancel()

    # --- EU trusted lists ----------------------------------------------------

    def _signers(self):
        return trusted_lists.lotl_signers(self._settings.trusted_list_lotl_signers_pem)

    async def refresh_trusted_lists(self, *, triggered_by: str) -> dict:
        """Fetches the LOTL and the national lists, stores those that pass.
        One at a time; a second call while one runs waits for it."""
        async with self._fetch_lock:
            async with self._session_factory() as session:
                fetch = await incoming_repository.start_fetch(
                    session, source="fetch", triggered_by=triggered_by
                )
                await session.commit()
                fetch_id = fetch.id
            s = self._settings
            limits = FetchLimits(
                per_request_timeout_seconds=s.trusted_list_fetch_timeout_seconds,
                max_response_bytes=s.trusted_list_max_bytes,
                proxy=s.signature_validation_http_proxy,
                extra_blocked_networks=list(s.incoming_validation_blocked_networks),
                allow_local_test_server=self.allow_local_test_server,
            )
            territories = {t.upper() for t in s.trusted_list_territories} or None

            async def _run():
                session = build_fenced_session(limits, same_host_redirects=False, decompress=True)
                try:
                    return await trusted_lists.fetch_and_check(
                        session,
                        lotl_url=self.lotl_url or s.trusted_list_lotl_url,
                        signers=self._signers(),
                        territories=territories,
                        max_bytes=s.trusted_list_max_bytes,
                    )
                finally:
                    await session.close()

            stored = 0
            try:
                # Parsing and checking the lists is CPU work: its own thread
                # and loop, so signing and validation stay responsive.
                checked, errors = await asyncio.to_thread(lambda: asyncio.run(_run()))
                async with self._session_factory() as session:
                    for item in checked:
                        await self._store(session, item, source="fetch")
                        stored += 1
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 - recorded on the fetch row
                logger.warning("trusted_list_fetch_failed", exc_info=True)
                errors = [f"{type(exc).__name__}: {exc}"[:500]]
            async with self._session_factory() as session:
                row = await session.get(TrustedListFetch, fetch_id)
                row.finished_at = datetime.now(UTC)
                row.lists_stored = stored
                row.errors = errors
                await session.commit()
            return {"lists_stored": stored, "errors": errors}

    async def _store(self, session, item: trusted_lists.CheckedList, *, source: str) -> None:
        info = item.info
        await incoming_repository.store_trusted_list(
            session,
            kind=info.kind,
            territory=info.territory,
            location_uri=item.location_uri,
            xml=item.xml,
            sha256=info.sha256,
            sequence_number=info.sequence_number,
            issued_at=info.issued_at,
            next_update=info.next_update,
            source=source,
        )

    async def import_trusted_list(self, xml: str, *, triggered_by: str) -> trusted_lists.ListInfo:
        """Offline import of one list: the LOTL first, then national lists,
        which are checked against the stored LOTL."""
        info = await asyncio.to_thread(trusted_lists.describe, xml)
        async with self._session_factory() as session:
            if info.kind == "lotl":
                await asyncio.to_thread(trusted_lists.check_lotl, xml, self._signers())
                location = None
            else:
                lotl_row = await incoming_repository.get_lotl(session)
                if lotl_row is None:
                    raise trusted_lists.TrustedListError(
                        "Zuerst die EU-Liste der Listen (LOTL) importieren"
                    )
                lotl = await asyncio.to_thread(
                    trusted_lists.check_lotl, lotl_row.xml, self._signers()
                )
                info, location = await asyncio.to_thread(trusted_lists.check_tl, xml, lotl)
            await self._store(
                session, trusted_lists.CheckedList(info, xml, location), source="import"
            )
            fetch = await incoming_repository.start_fetch(
                session, source="import", triggered_by=triggered_by
            )
            fetch.finished_at = datetime.now(UTC)
            fetch.lists_stored = 1
            await session.commit()
        return info

    # Overridable for tests (a local list server).
    lotl_url: str | None = None
    allow_local_test_server: bool = False
