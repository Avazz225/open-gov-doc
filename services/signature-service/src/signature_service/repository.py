from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from signature_service.connectors import generate_root_ca, issue_tsa_certificate
from signature_service.models import InternalCa, InternalTsa, Signature, SignatureConfig

_CA_ID = 1
_TSA_ID = 1
_SIGNATURE_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


class InvalidProviderLevelsError(Exception):
    pass


async def get_or_create_ca(session: AsyncSession) -> InternalCa:
    """Singleton pattern like `OcrConfig`/`SystemMaintenanceMode`: the
    internal root CA is generated on the very first startup and then reused
    idempotently - a restart must not generate a new CA, otherwise
    previously issued signatures would no longer be verifiable against the
    (then different) root."""
    ca = await session.get(InternalCa, _CA_ID)
    if ca is not None:
        return ca
    certificate_pem, private_key_pem = generate_root_ca()
    ca = InternalCa(
        id=_CA_ID,
        certificate_pem=certificate_pem,
        private_key_pem=private_key_pem,
        created_at=datetime.now(UTC),
    )
    session.add(ca)
    await session.flush()
    return ca


async def get_or_create_tsa(
    session: AsyncSession, *, ca_certificate_pem: bytes, ca_private_key_pem: bytes
) -> InternalTsa:
    """Singleton pattern like `get_or_create_ca` (3.10, PAdES-B-LTA,
    Post-Roadmap Phase 41 Session 1) - the internal TSA certificate is
    issued once, from the already-existing root CA, and then reused
    idempotently for the same reason as the CA itself: a restart must not
    reissue it, or previously embedded timestamp tokens would no longer
    resolve to a trusted signer."""
    tsa = await session.get(InternalTsa, _TSA_ID)
    if tsa is not None:
        return tsa
    certificate_pem, private_key_pem = issue_tsa_certificate(ca_certificate_pem, ca_private_key_pem)
    tsa = InternalTsa(
        id=_TSA_ID,
        certificate_pem=certificate_pem,
        private_key_pem=private_key_pem,
        created_at=datetime.now(UTC),
    )
    session.add(tsa)
    await session.flush()
    return tsa


async def create_signature(
    session: AsyncSession,
    *,
    document_id: str,
    source_version_number: int,
    version_number: int,
    level: str,
    connector_id: str,
    signer_principal_id: str,
    signer_display_name: str,
    certificate_subject: str,
    certificate_serial: str,
    certificate_not_before: datetime,
    certificate_not_after: datetime,
    reason: str | None,
) -> Signature:
    signature = Signature(
        document_id=document_id,
        source_version_number=source_version_number,
        version_number=version_number,
        level=level,
        connector_id=connector_id,
        signer_principal_id=signer_principal_id,
        signer_display_name=signer_display_name,
        certificate_subject=certificate_subject,
        certificate_serial=certificate_serial,
        certificate_not_before=certificate_not_before,
        certificate_not_after=certificate_not_after,
        reason=reason,
        signed_at=datetime.now(UTC),
    )
    session.add(signature)
    await session.flush()
    return signature


async def get_signature(session: AsyncSession, signature_id: int) -> Signature:
    signature = await session.get(Signature, signature_id)
    if signature is None:
        raise NotFoundError(f"signature_id {signature_id!r} unbekannt")
    return signature


async def list_signatures(
    session: AsyncSession, *, document_id: str | None = None
) -> list[Signature]:
    query = select(Signature)
    if document_id is not None:
        query = query.where(Signature.document_id == document_id)
    result = await session.execute(query.order_by(Signature.signed_at.desc()))
    return list(result.scalars().all())


async def list_signatures_due_for_retimestamp(
    session: AsyncSession, *, cutoff: datetime
) -> Sequence[Signature]:
    """Signatures whose archive-timestamp chain needs extending (PAdES-
    B-LTA, 3.10, Post-Roadmap Phase 41 Session 1) - either never extended
    yet and signed before `cutoff`, or last extended before `cutoff`.
    Called from `main.py`'s periodic poll loop with `cutoff = now -
    Settings.retimestamp_interval_days`."""
    query = select(Signature).where(
        or_(
            and_(Signature.last_timestamped_at.is_(None), Signature.signed_at <= cutoff),
            Signature.last_timestamped_at <= cutoff,
        )
    )
    result = await session.execute(query)
    return result.scalars().all()


async def mark_timestamped(
    session: AsyncSession, signature_id: int, *, version_number: int
) -> None:
    """Records a successful archive-timestamp-chain extension - advances
    `version_number` to the new document version the extended bytes were
    checked in as (same "signed bytes get their own version" principle as
    the original signature) and stamps `last_timestamped_at`."""
    signature = await get_signature(session, signature_id)
    signature.version_number = version_number
    signature.last_timestamped_at = datetime.now(UTC)
    await session.flush()


async def get_signature_config(
    session: AsyncSession, *, default_provider_levels: dict[str, list[str]]
) -> SignatureConfig:
    """Get-or-create (post-roadmap phase 22 session 6, ADR 0091), same
    pattern as `storage_service.repository.get_operational_config`. The
    default is passed in as a parameter by the caller (`main.py`, from
    `Settings.signature_providers`), so that this module stays free of any
    env-var knowledge."""
    config = await session.get(SignatureConfig, _SIGNATURE_CONFIG_ID)
    if config is None:
        config = SignatureConfig(
            id=_SIGNATURE_CONFIG_ID,
            provider_levels=default_provider_levels,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_signature_config(
    session: AsyncSession,
    *,
    provider_levels: dict[str, list[str]],
    known_provider_types: dict[str, str],
    default_provider_levels: dict[str, list[str]],
) -> SignatureConfig:
    """Updates ONLY the connector `id`s named in the call (partial merge,
    connectors not named keep their current value) - matching
    `SignatureProviderLevelsIn`'s list shape, an admin doesn't have to send
    all connectors every time. `known_provider_types` (id->type from
    `Settings.signature_providers`) restricts to connectors already
    configured via env var ("only edit existing entries") and passes
    `type` through for the same validation as
    `SignatureProviderConfig._check_levels` (settings schema validator)."""
    for provider_id, levels in provider_levels.items():
        if provider_id not in known_provider_types:
            raise InvalidProviderLevelsError(f"Unbekannter Connector: {provider_id!r}")
        if not levels:
            raise InvalidProviderLevelsError(
                f"Connector {provider_id!r}: levels darf nicht leer sein"
            )
        if known_provider_types[provider_id] == "internal" and "qes" in levels:
            raise InvalidProviderLevelsError(
                f"Connector {provider_id!r}: type=internal kann kein QES ausstellen"
            )

    config = await get_signature_config(session, default_provider_levels=default_provider_levels)
    config.provider_levels = {**config.provider_levels, **provider_levels}
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config
