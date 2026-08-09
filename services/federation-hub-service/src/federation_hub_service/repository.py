from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from federation_hub_service import crypto_utils
from federation_hub_service.models import Handover, HubIdentity, Installation
from federation_hub_service.schemas import InstallationRegister
from federation_hub_service.version_utils import parse_version

_HUB_IDENTITY_ID = 1


class UnauthorizedError(Exception):
    """Fehlender/ungültiger API-Key, oder ein Update-Versuch einer bereits
    bestehenden Installation mit dem falschen Key."""


class NotFoundError(Exception):
    pass


class VersionIncompatibleError(Exception):
    pass


async def get_or_create_hub_identity(session: AsyncSession) -> HubIdentity:
    """Singleton-Muster wie `signature-service`s `get_or_create_ca`: das
    Signaturschlüsselpaar des Hub wird beim allerersten Start generiert und
    danach idempotent wiederverwendet - ein Neustart darf keinen neuen
    Schlüssel erzeugen, sonst könnten bereits registrierte Installationen den
    (dann anderen) öffentlichen Schlüssel nicht mehr verifizieren."""
    identity = await session.get(HubIdentity, _HUB_IDENTITY_ID)
    if identity is not None:
        return identity
    private_pem, public_pem = crypto_utils.generate_hub_keypair()
    identity = HubIdentity(
        id=_HUB_IDENTITY_ID,
        private_key_pem=private_pem,
        public_key_pem=public_pem,
        created_at=datetime.now(UTC),
    )
    session.add(identity)
    await session.flush()
    return identity


async def register_or_update_installation(
    session: AsyncSession, payload: InstallationRegister, *, presented_api_key: str | None
) -> tuple[Installation, str | None]:
    """Registrierung ist ein Upsert (analog `registry-service.register`),
    aber - anders als dort - für eine bereits bekannte ``id`` nur mit einem
    zum gespeicherten Hash passenden API-Key (verhindert, dass ein beliebiger
    Aufrufer den Adressbucheintrag einer fremden Installation überschreibt).
    Gibt bei einer Neuanlage den einmaligen Klartext-API-Key zurück, sonst
    ``None`` (der Hub speichert ihn nie im Klartext, kann ihn also bei einem
    Update nicht erneut ausgeben)."""
    now = datetime.now(UTC)
    existing = await session.get(Installation, payload.id)
    if existing is None:
        api_key = crypto_utils.generate_api_key()
        installation = Installation(
            id=payload.id,
            display_name=payload.display_name,
            callback_base_url=payload.callback_base_url,
            public_key_pem=payload.public_key_pem,
            api_key_hash=crypto_utils.hash_api_key(api_key),
            version=payload.version,
            min_compatible_peer_version=payload.min_compatible_peer_version,
            supported_process_types=payload.supported_process_types,
            supported_document_types=payload.supported_document_types,
            registered_at=now,
            updated_at=now,
        )
        session.add(installation)
        await session.flush()
        return installation, api_key

    presented_hash = presented_api_key and crypto_utils.hash_api_key(presented_api_key)
    if not presented_hash or presented_hash != existing.api_key_hash:
        raise UnauthorizedError("API-Key stimmt nicht mit der registrierten Installation überein")

    existing.display_name = payload.display_name
    existing.callback_base_url = payload.callback_base_url
    existing.public_key_pem = payload.public_key_pem
    existing.version = payload.version
    existing.min_compatible_peer_version = payload.min_compatible_peer_version
    existing.supported_process_types = payload.supported_process_types
    existing.supported_document_types = payload.supported_document_types
    existing.updated_at = now
    await session.flush()
    return existing, None


async def deregister_installation(
    session: AsyncSession, installation_id: str, *, presented_api_key: str | None
) -> None:
    installation = await session.get(Installation, installation_id)
    if installation is None:
        raise NotFoundError(f"installation_id {installation_id!r} unbekannt")
    if (
        not presented_api_key
        or crypto_utils.hash_api_key(presented_api_key) != installation.api_key_hash
    ):
        raise UnauthorizedError("API-Key stimmt nicht mit der registrierten Installation überein")
    await session.delete(installation)
    await session.flush()


async def list_installations(session: AsyncSession) -> list[Installation]:
    result = await session.execute(select(Installation).order_by(Installation.id))
    return list(result.scalars().all())


async def get_installation_by_api_key(session: AsyncSession, api_key: str) -> Installation | None:
    result = await session.execute(
        select(Installation).where(Installation.api_key_hash == crypto_utils.hash_api_key(api_key))
    )
    return result.scalar_one_or_none()


def is_version_compatible(a: Installation, b: Installation) -> bool:
    """Beidseitige Prüfung (7.4 "Versionskompatibilität"): ``b`` muss mindestens
    ``a``s erklärte Mindestanforderung erfüllen und umgekehrt. Bewusst ein
    einfaches ``(major, minor)``-Zahlenschema statt einer SemVer-Bibliothek.
    ``parse_version`` kann hier nicht mehr fehlschlagen (P13-S3: Format wird
    bereits bei der Registrierung validiert, siehe `schemas.py`)."""
    return parse_version(b.version) >= parse_version(a.min_compatible_peer_version) and (
        parse_version(a.version) >= parse_version(b.min_compatible_peer_version)
    )


async def create_handover(
    session: AsyncSession,
    *,
    handover_id: str,
    from_installation_id: str,
    to_installation_id: str,
    process_type: str,
) -> Handover:
    """``handover_id`` kommt von der Absenderinstallation selbst (siehe
    `schemas.HandoverCreate`), nicht vom Hub generiert - siehe ADR 0028
    "Selbst-Loopback"."""
    handover = Handover(
        id=handover_id,
        from_installation_id=from_installation_id,
        to_installation_id=to_installation_id,
        process_type=process_type,
        status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(handover)
    await session.flush()
    return handover


async def get_handover(session: AsyncSession, handover_id: str) -> Handover:
    handover = await session.get(Handover, handover_id)
    if handover is None:
        raise NotFoundError(f"handover_id {handover_id!r} unbekannt")
    return handover


async def mark_handover_delivered(
    session: AsyncSession, handover: Handover, *, success: bool
) -> None:
    handover.status = "delivered" if success else "delivery_failed"
    handover.delivered_at = datetime.now(UTC)
    await session.flush()


async def mark_handover_result_delivered(
    session: AsyncSession, handover: Handover, *, success: bool
) -> None:
    handover.status = "completed" if success else "result_delivery_failed"
    handover.completed_at = datetime.now(UTC)
    await session.flush()
