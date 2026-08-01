from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from signature_service.connectors import generate_root_ca
from signature_service.models import InternalCa, Signature

_CA_ID = 1


class NotFoundError(Exception):
    pass


async def get_or_create_ca(session: AsyncSession) -> InternalCa:
    """Singleton-Muster wie `OcrConfig`/`SystemMaintenanceMode`: die interne
    Root-CA wird beim allerersten Start generiert und danach idempotent
    wiederverwendet - ein Neustart darf keine neue CA erzeugen, sonst würden
    zuvor ausgestellte Signaturen nicht mehr gegen die (dann andere) Root
    verifizierbar sein."""
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
