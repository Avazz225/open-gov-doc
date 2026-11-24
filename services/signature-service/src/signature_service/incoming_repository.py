"""Persistence for incoming-signature validation (P92-S1, ADR 0248)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from signature_service.models import (
    INCOMING_VALIDATION_CONFIG_ID,
    IncomingSignatureSummary,
    IncomingSignatureValidation,
    IncomingTrustAnchor,
    IncomingValidationConfig,
    TrustedList,
    TrustedListFetch,
)
from signature_service.validation.interface import (
    INDICATION_RANK,
    QUALIFICATION_RANK,
    ValidationOutcome,
)


class NotFoundError(Exception):
    pass


class DuplicateAnchorError(Exception):
    pass


async def get_config(session: AsyncSession) -> IncomingValidationConfig:
    config = await session.get(IncomingValidationConfig, INCOMING_VALIDATION_CONFIG_ID)
    if config is None:
        config = IncomingValidationConfig(
            id=INCOMING_VALIDATION_CONFIG_ID,
            enabled=True,
            revocation_mode="soft-fail",
            outbound_fetching=True,
            eu_trusted_lists=True,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_config(
    session: AsyncSession,
    *,
    enabled: bool,
    revocation_mode: str,
    outbound_fetching: bool,
    eu_trusted_lists: bool,
    updated_by: str,
) -> IncomingValidationConfig:
    config = await get_config(session)
    config.eu_trusted_lists = eu_trusted_lists
    config.enabled = enabled
    config.revocation_mode = revocation_mode
    config.outbound_fetching = outbound_fetching
    config.updated_by = updated_by
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def list_anchors(session: AsyncSession) -> list[IncomingTrustAnchor]:
    result = await session.execute(select(IncomingTrustAnchor).order_by(IncomingTrustAnchor.id))
    return list(result.scalars())


async def add_anchor(
    session: AsyncSession,
    *,
    label: str,
    certificate_der: bytes,
    fingerprint_sha256: str,
    subject: str,
    not_after: datetime,
    created_by: str,
) -> IncomingTrustAnchor:
    existing = await session.execute(
        select(IncomingTrustAnchor).where(
            IncomingTrustAnchor.fingerprint_sha256 == fingerprint_sha256
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateAnchorError("Dieses Zertifikat ist bereits als Vertrauensanker hinterlegt")
    anchor = IncomingTrustAnchor(
        label=label,
        certificate_der=certificate_der,
        fingerprint_sha256=fingerprint_sha256,
        subject=subject,
        not_after=not_after,
        created_at=datetime.now(UTC),
        created_by=created_by,
    )
    session.add(anchor)
    await session.flush()
    return anchor


async def delete_anchor(session: AsyncSession, anchor_id: int) -> IncomingTrustAnchor:
    anchor = await session.get(IncomingTrustAnchor, anchor_id)
    if anchor is None:
        raise NotFoundError(f"Vertrauensanker {anchor_id} unbekannt")
    await session.delete(anchor)
    await session.flush()
    return anchor


async def record_run(
    session: AsyncSession,
    *,
    document_id: str,
    version_number: int,
    trigger: str,
    revocation_mode: str,
    outcome: ValidationOutcome,
    run_id: str | None = None,
    report_key: str | None = None,
) -> IncomingSignatureSummary | None:
    """Stores one run: a row per finding and the version's summary. A run
    without findings (an unsigned PDF) stores nothing - there is nothing to
    show, and an earlier summary for the version stays as it was."""
    if not outcome.findings:
        return None
    run_id = run_id or str(uuid.uuid4())
    now = datetime.now(UTC)
    for finding in outcome.findings:
        session.add(
            IncomingSignatureValidation(
                run_id=run_id,
                document_id=document_id,
                version_number=version_number,
                trigger=trigger,
                format=finding.format,
                position=finding.position,
                kind=finding.kind,
                indication=finding.indication,
                sub_indication=finding.sub_indication,
                qualification=finding.qualification,
                trust_source=finding.trust_source,
                signer_subject=finding.signer_subject,
                signer_issuer=finding.signer_issuer,
                signer_serial=finding.signer_serial,
                claimed_signing_time=finding.claimed_signing_time,
                best_signature_time=finding.best_signature_time,
                coverage=finding.coverage,
                modification_level=finding.modification_level,
                qc_type=finding.qc_type,
                report_key=report_key,
                failure_reason=finding.failure_reason,
                technical_detail=finding.technical_detail,
                revocation_mode=revocation_mode,
                validated_at=now,
            )
        )
    worst = min(outcome.findings, key=lambda f: INDICATION_RANK[f.indication]).indication
    best = max(outcome.findings, key=lambda f: QUALIFICATION_RANK[f.qualification]).qualification
    summary = IncomingSignatureSummary(
        document_id=document_id,
        version_number=version_number,
        run_id=run_id,
        # Document timestamps aren't signatures of a person.
        signature_count=sum(1 for f in outcome.findings if f.kind != "document_timestamp"),
        worst_indication=worst,
        best_qualification=best,
        changed_after_signing=any(f.coverage == "changed_after_signing" for f in outcome.findings),
        truncated=outcome.truncated,
        validated_at=now,
    )
    await session.execute(
        delete(IncomingSignatureSummary).where(
            IncomingSignatureSummary.document_id == document_id,
            IncomingSignatureSummary.version_number == version_number,
        )
    )
    session.add(summary)
    await session.flush()
    return summary


async def list_summaries(session: AsyncSession, document_id: str) -> list[IncomingSignatureSummary]:
    result = await session.execute(
        select(IncomingSignatureSummary)
        .where(IncomingSignatureSummary.document_id == document_id)
        .order_by(IncomingSignatureSummary.version_number)
    )
    return list(result.scalars())


async def list_summaries_for(
    session: AsyncSession, document_ids: list[str]
) -> list[IncomingSignatureSummary]:
    result = await session.execute(
        select(IncomingSignatureSummary)
        .where(IncomingSignatureSummary.document_id.in_(document_ids))
        .order_by(IncomingSignatureSummary.document_id, IncomingSignatureSummary.version_number)
    )
    return list(result.scalars())


async def list_validations(
    session: AsyncSession,
    document_id: str,
    *,
    version_number: int | None = None,
    latest_only: bool = True,
) -> list[IncomingSignatureValidation]:
    query = select(IncomingSignatureValidation).where(
        IncomingSignatureValidation.document_id == document_id
    )
    if version_number is not None:
        query = query.where(IncomingSignatureValidation.version_number == version_number)
    if latest_only:
        latest_runs = select(IncomingSignatureSummary.run_id).where(
            IncomingSignatureSummary.document_id == document_id
        )
        query = query.where(IncomingSignatureValidation.run_id.in_(latest_runs))
    query = query.order_by(
        IncomingSignatureValidation.version_number,
        IncomingSignatureValidation.validated_at,
        IncomingSignatureValidation.id,
    )
    result = await session.execute(query)
    return list(result.scalars())


async def get_run(session: AsyncSession, run_id: str) -> IncomingSignatureValidation:
    """Any row of a run (they share document, version and report key)."""
    result = await session.execute(
        select(IncomingSignatureValidation)
        .where(IncomingSignatureValidation.run_id == run_id)
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Prüflauf {run_id} unbekannt")
    return row


async def validated_versions(
    session: AsyncSession, keys: list[tuple[str, int]]
) -> set[tuple[str, int]]:
    """Which (document, version) pairs already have a summary - the backfill
    skips them unless asked not to."""
    if not keys:
        return set()
    document_ids = {d for d, _ in keys}
    result = await session.execute(
        select(IncomingSignatureSummary.document_id, IncomingSignatureSummary.version_number).where(
            IncomingSignatureSummary.document_id.in_(document_ids)
        )
    )
    return {(d, v) for d, v in result.all()} & set(keys)


# --- EU trusted lists (P92-S2) ---------------------------------------------


async def store_trusted_list(
    session: AsyncSession,
    *,
    kind: str,
    territory: str,
    location_uri: str | None,
    xml: str,
    sha256: str,
    sequence_number: int | None,
    issued_at: datetime | None,
    next_update: datetime | None,
    source: str,
) -> TrustedList:
    """Replaces the stored list for (kind, territory)."""
    await session.execute(
        delete(TrustedList).where(TrustedList.kind == kind, TrustedList.territory == territory)
    )
    row = TrustedList(
        kind=kind,
        territory=territory,
        location_uri=location_uri,
        xml=xml,
        sha256=sha256,
        sequence_number=sequence_number,
        issued_at=issued_at,
        next_update=next_update,
        fetched_at=datetime.now(UTC),
        source=source,
    )
    session.add(row)
    await session.flush()
    return row


async def get_lotl(session: AsyncSession) -> TrustedList | None:
    result = await session.execute(select(TrustedList).where(TrustedList.kind == "lotl"))
    return result.scalar_one_or_none()


async def list_trusted_lists(session: AsyncSession) -> list[TrustedList]:
    result = await session.execute(
        select(TrustedList).order_by(TrustedList.kind, TrustedList.territory)
    )
    return list(result.scalars())


async def trusted_list_generation(session: AsyncSession) -> str:
    """Changes whenever a national list is stored or replaced - the key of
    the in-memory registry cache."""
    result = await session.execute(
        select(TrustedList.id, TrustedList.sha256)
        .where(TrustedList.kind == "tl")
        .order_by(TrustedList.id)
    )
    return ",".join(f"{i}:{h[:12]}" for i, h in result.all())


async def national_list_xmls(session: AsyncSession) -> list[str]:
    result = await session.execute(select(TrustedList.xml).where(TrustedList.kind == "tl"))
    return list(result.scalars())


async def start_fetch(session: AsyncSession, *, source: str, triggered_by: str) -> TrustedListFetch:
    fetch = TrustedListFetch(
        started_at=datetime.now(UTC), source=source, triggered_by=triggered_by, errors=[]
    )
    session.add(fetch)
    await session.flush()
    return fetch


async def last_fetch(session: AsyncSession) -> TrustedListFetch | None:
    result = await session.execute(
        select(TrustedListFetch).order_by(TrustedListFetch.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()
