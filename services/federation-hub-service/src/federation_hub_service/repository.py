from datetime import UTC, datetime, timedelta

from dms_retry import compute_backoff_seconds
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from federation_hub_service import crypto_utils
from federation_hub_service.models import Handover, HubIdentity, Installation
from federation_hub_service.schemas import InstallationRegister
from federation_hub_service.version_utils import parse_version

_HUB_IDENTITY_ID = 1


class UnauthorizedError(Exception):
    """Fehlende/ungültige Installations-Signatur, ein Update-/Rotationsversuch
    mit dem falschen Schlüssel, oder eine widerrufene Installation."""


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
    session: AsyncSession,
    payload: InstallationRegister,
    *,
    raw_body: bytes,
    presented_signature: str | None,
) -> Installation:
    """Registrierung ist ein Upsert (analog `registry-service.register`),
    aber seit P13-S4 (ADR 0039) kryptografisch statt über ein geteiltes
    Geheimnis abgesichert:

    - **Neuanlage**: die Signatur muss zum im selben Request eingereichten
      ``public_key_pem`` passen (Selbstkonsistenz-Nachweis - die Installation
      muss den privaten Schlüssel zu dem Schlüssel besitzen, den sie gerade
      registriert, sonst könnte jeder einen beliebigen fremden öffentlichen
      Schlüssel im Namen einer neuen ``id`` hinterlegen).
    - **Update**: die Signatur muss zum **bereits gespeicherten**
      ``public_key_pem`` passen - verhindert, dass ein beliebiger Aufrufer den
      Adressbucheintrag einer fremden Installation überschreibt.
      ``public_key_pem`` selbst wird bei einem Update **nicht** übernommen
      (ein abweichender Wert im Payload wird schlicht ignoriert) - ein
      Schlüsselwechsel läuft ausschließlich über
      ``POST /installations/{id}/rotate-key``, damit eine routinemäßige
      Re-Registrierung (z. B. nach einer Versionsänderung) nicht versehentlich
      auch die kryptografische Identität mitändert.
    """
    now = datetime.now(UTC)
    existing = await session.get(Installation, payload.id)
    if existing is None:
        if not presented_signature or not crypto_utils.verify_body(
            payload.public_key_pem, raw_body, presented_signature
        ):
            raise UnauthorizedError("Signatur passt nicht zum eingereichten öffentlichen Schlüssel")
        installation = Installation(
            id=payload.id,
            display_name=payload.display_name,
            callback_base_url=payload.callback_base_url,
            public_key_pem=payload.public_key_pem,
            version=payload.version,
            min_compatible_peer_version=payload.min_compatible_peer_version,
            supported_process_types=payload.supported_process_types,
            supported_document_types=payload.supported_document_types,
            registered_at=now,
            updated_at=now,
        )
        session.add(installation)
        await session.flush()
        return installation

    if existing.revoked_at is not None:
        raise UnauthorizedError("Installation wurde widerrufen")
    if not presented_signature or not crypto_utils.verify_body(
        existing.public_key_pem, raw_body, presented_signature
    ):
        raise UnauthorizedError("Signatur passt nicht zum registrierten öffentlichen Schlüssel")

    existing.display_name = payload.display_name
    existing.callback_base_url = payload.callback_base_url
    existing.version = payload.version
    existing.min_compatible_peer_version = payload.min_compatible_peer_version
    existing.supported_process_types = payload.supported_process_types
    existing.supported_document_types = payload.supported_document_types
    existing.updated_at = now
    await session.flush()
    return existing


async def deregister_installation(
    session: AsyncSession, installation_id: str, *, presented_signature: str | None
) -> None:
    """Signatur über die UTF-8-Bytes von ``installation_id`` selbst (keine
    andere natürliche "Body" für einen `DELETE` ohne Payload, gleiche
    Konvention wie unten bei `rotate_installation_key`s Aufrufer)."""
    installation = await session.get(Installation, installation_id)
    if installation is None:
        raise NotFoundError(f"installation_id {installation_id!r} unbekannt")
    if not presented_signature or not crypto_utils.verify_body(
        installation.public_key_pem, installation_id.encode("utf-8"), presented_signature
    ):
        raise UnauthorizedError("Signatur passt nicht zum registrierten öffentlichen Schlüssel")
    await session.delete(installation)
    await session.flush()


async def rotate_installation_key(
    session: AsyncSession,
    installation_id: str,
    *,
    raw_body: bytes,
    new_public_key_pem: str,
    presented_signature: str | None,
) -> Installation:
    """Kontrollierter Schlüsselwechsel (P13-S4, ADR 0039 "Schlüsselrotation"):
    ``raw_body`` (der `RotateKeyRequest`-Body, enthält ``new_public_key_pem``)
    muss mit dem **aktuellen** (alten) privaten Schlüssel signiert sein - ein
    Kontinuitätsnachweis, der beweist, dass der Rotationswunsch tatsächlich
    von der Installation selbst kommt, nicht von einem Dritten, der zufällig
    die ``id`` kennt."""
    installation = await session.get(Installation, installation_id)
    if installation is None:
        raise NotFoundError(f"installation_id {installation_id!r} unbekannt")
    if installation.revoked_at is not None:
        raise UnauthorizedError("Installation wurde widerrufen")
    if not presented_signature or not crypto_utils.verify_body(
        installation.public_key_pem, raw_body, presented_signature
    ):
        raise UnauthorizedError("Signatur passt nicht zum aktuellen öffentlichen Schlüssel")
    installation.public_key_pem = new_public_key_pem
    installation.updated_at = datetime.now(UTC)
    await session.flush()
    return installation


async def revoke_installation(
    session: AsyncSession, installation_id: str, *, reason: str | None
) -> Installation:
    """Betreiber-Aktion (P13-S4, ADR 0039 "Revocation") - bewusst **ohne**
    Signaturprüfung der betroffenen Installation: der ganze Sinn des
    Widerrufs ist der Fall, in dem die Installation selbst nicht mehr
    vertrauenswürdig signieren kann (kompromittierter privater Schlüssel).
    Gegated stattdessen über `settings.hub_operator_key`, siehe `main.py`."""
    installation = await session.get(Installation, installation_id)
    if installation is None:
        raise NotFoundError(f"installation_id {installation_id!r} unbekannt")
    installation.revoked_at = datetime.now(UTC)
    installation.revoked_reason = reason
    await session.flush()
    return installation


async def list_installations(session: AsyncSession) -> list[Installation]:
    result = await session.execute(select(Installation).order_by(Installation.id))
    return list(result.scalars().all())


async def authenticate_signed_request(
    session: AsyncSession, *, installation_id: str, body: bytes, signature: str
) -> Installation:
    """Zentrale Verifikation für jede signierte Installations-Anfrage
    (`POST /handovers`, `.../result`) - Gegenstück zu `_verify_hub_signature`
    auf der Installationsseite (`workflow_service.main`), nur in umgekehrter
    Richtung."""
    if not installation_id or not signature:
        raise UnauthorizedError("Fehlende Installations-Signatur")
    installation = await session.get(Installation, installation_id)
    if installation is None:
        raise UnauthorizedError("Unbekannte Installation")
    if installation.revoked_at is not None:
        raise UnauthorizedError("Installation wurde widerrufen")
    if not crypto_utils.verify_body(installation.public_key_pem, body, signature):
        raise UnauthorizedError("Ungültige Installations-Signatur")
    return installation


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
    session: AsyncSession, handover: Handover, *, success: bool, max_attempts: int
) -> None:
    """Retry-aware Fassung (P20-S5, ADR 0081): ein Fehlschlag führt nicht mehr
    sofort zu ``delivery_failed``, sondern zu ``pending_retry`` mit
    Full-Jitter-Backoff, solange ``max_attempts`` noch nicht erreicht ist -
    gleiches Muster wie `ocr_service.repository.record_failure` /
    `rendering_service.repository.record_failure` (ADR 0080)."""
    if success:
        handover.status = "delivered"
        handover.delivered_at = datetime.now(UTC)
        handover.next_retry_at = None
        await session.flush()
        return
    handover.attempts += 1
    if handover.attempts >= max_attempts:
        handover.status = "delivery_failed"
        handover.next_retry_at = None
    else:
        handover.status = "pending_retry"
        delay = compute_backoff_seconds(handover.attempts - 1)
        handover.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    await session.flush()


async def list_due_for_retry(session: AsyncSession) -> list[Handover]:
    now = datetime.now(UTC)
    result = await session.execute(
        select(Handover).where(
            Handover.status == "pending_retry",
            Handover.next_retry_at <= now,
        )
    )
    return list(result.scalars().all())


async def reset_for_retry(session: AsyncSession, handover: Handover) -> None:
    """MUSS vor einem erneuten Zustellversuch laufen - sonst zählt
    `mark_handover_delivered` von der bereits erschöpften attempts-Zahl
    weiter (siehe ADR 0080 "Konsequenzen" für den gefundenen Bug in
    ocr-service/rendering-service, der dieses Muster begründet hat)."""
    handover.attempts = 0
    handover.next_retry_at = None
    await session.flush()


async def mark_handover_result_delivered(
    session: AsyncSession, handover: Handover, *, success: bool
) -> None:
    handover.status = "completed" if success else "result_delivery_failed"
    handover.completed_at = datetime.now(UTC)
    await session.flush()
