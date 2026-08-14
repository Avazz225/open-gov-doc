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
    """Missing/invalid installation signature, an update/rotation attempt
    with the wrong key, or a revoked installation."""


class NotFoundError(Exception):
    pass


class VersionIncompatibleError(Exception):
    pass


async def get_or_create_hub_identity(session: AsyncSession) -> HubIdentity:
    """Singleton pattern like `signature-service`'s `get_or_create_ca`: the
    hub's signing key pair is generated on the very first startup and then
    reused idempotently - a restart must not generate a new key, otherwise
    already-registered installations could no longer verify the (then
    different) public key.

    Since Post-Roadmap Phase 21 Session 2 (ADR 0085), ``ca_certificate_pem``
    is additionally ensured - immediately for a freshly generated key pair,
    lazily backfilled for a row that already existed BEFORE this session
    (migration, ``ca_certificate_pem IS NULL``), without changing the key
    pair itself (a pure certificate wrapper around the already-existing key,
    see `crypto_utils.generate_ca_certificate`)."""
    identity = await session.get(HubIdentity, _HUB_IDENTITY_ID)
    if identity is None:
        private_pem, public_pem = crypto_utils.generate_hub_keypair()
        ca_certificate_pem = crypto_utils.generate_ca_certificate(private_pem, public_pem)
        identity = HubIdentity(
            id=_HUB_IDENTITY_ID,
            private_key_pem=private_pem,
            public_key_pem=public_pem,
            ca_certificate_pem=ca_certificate_pem,
            created_at=datetime.now(UTC),
        )
        session.add(identity)
        await session.flush()
        return identity
    if identity.ca_certificate_pem is None:
        identity.ca_certificate_pem = crypto_utils.generate_ca_certificate(
            identity.private_key_pem, identity.public_key_pem
        )
        await session.flush()
    return identity


async def issue_or_renew_installation_certificate(
    session: AsyncSession,
    installation: Installation,
    *,
    ca_certificate_pem: bytes,
    ca_private_key_pem: bytes,
) -> None:
    """Issues a new certificate, signed by the hub CA, for
    ``installation.public_key_pem`` (Post-Roadmap Phase 21 Session 2,
    ADR 0085) - called on registration, key rotation, and when backfilling
    legacy installations without a certificate (see `main.lifespan`). MUST be
    called again after every change to ``public_key_pem`` - a certificate for
    the OLD key would no longer be valid after a rotation."""
    certificate_pem, not_valid_after = crypto_utils.issue_installation_certificate(
        ca_certificate_pem,
        ca_private_key_pem,
        installation_id=installation.id,
        installation_public_key_pem=installation.public_key_pem,
    )
    installation.certificate_pem = certificate_pem
    installation.certificate_not_after = not_valid_after
    await session.flush()


async def register_or_update_installation(
    session: AsyncSession,
    payload: InstallationRegister,
    *,
    raw_body: bytes,
    presented_signature: str | None,
) -> Installation:
    """Registration is an upsert (analogous to `registry-service.register`),
    but since P13-S4 (ADR 0039) secured cryptographically instead of via a
    shared secret:

    - **Creation**: the signature must match the ``public_key_pem`` submitted
      in the same request (self-consistency proof - the installation must own
      the private key for the key it is currently registering, otherwise
      anyone could deposit an arbitrary foreign public key under the name of
      a new ``id``).
    - **Update**: the signature must match the **already-stored**
      ``public_key_pem`` - prevents any arbitrary caller from overwriting the
      address-book entry of a foreign installation. ``public_key_pem`` itself
      is **not** adopted on an update (a differing value in the payload is
      simply ignored) - a key change goes exclusively through
      ``POST /installations/{id}/rotate-key``, so that a routine
      re-registration (e.g. after a version change) doesn't accidentally
      also change the cryptographic identity.
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
    """Signature over the UTF-8 bytes of ``installation_id`` itself (no other
    natural "body" for a `DELETE` without a payload, same convention as
    `rotate_installation_key`'s caller below)."""
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
    """Controlled key change (P13-S4, ADR 0039 "key rotation"): ``raw_body``
    (the `RotateKeyRequest` body, contains ``new_public_key_pem``) must be
    signed with the **current** (old) private key - a continuity proof that
    demonstrates the rotation request actually comes from the installation
    itself, not from a third party who happens to know the ``id``."""
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
    """Operator action (P13-S4, ADR 0039 "Revocation") - deliberately
    **without** a signature check of the affected installation: the whole
    point of revocation is the case where the installation itself can no
    longer sign in a trustworthy way (compromised private key). Gated
    instead via `settings.hub_operator_key`, see `main.py`."""
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


async def list_installations_without_certificate(session: AsyncSession) -> list[Installation]:
    """Backfill migration (ADR 0085) - installations registered before
    Post-Roadmap Phase 21 Session 2, see `main.lifespan`."""
    result = await session.execute(
        select(Installation).where(Installation.certificate_pem.is_(None))
    )
    return list(result.scalars().all())


async def authenticate_signed_request(
    session: AsyncSession,
    *,
    installation_id: str,
    body: bytes,
    signature: str,
    hub_ca_certificate_pem: bytes | None = None,
) -> Installation:
    """Central verification for every signed installation request
    (`POST /handovers`, `.../result`) - counterpart to
    `_verify_hub_signature` on the installation side (`workflow_service.
    main`), just in the reverse direction. Since Post-Roadmap Phase 21
    Session 2 (ADR 0085), in addition to the signature check, a real
    certificate check (chain up to the hub CA AND validity window, see
    `crypto_utils.verify_installation_certificate`) - deliberately IN
    ADDITION, not as a replacement: the signature check remains the actual
    proof of possession, the certificate gives it a verified origin and a
    validity boundary. Installations without a certificate
    (``certificate_pem IS NULL``, legacy rows from before this session, see
    `main.lifespan`) or a caller without ``hub_ca_certificate_pem`` (e.g. a
    test that deliberately omits this parameter) skip this additional check
    (grandfathering) - in practice this should no longer occur after the
    backfill migration step."""
    if not installation_id or not signature:
        raise UnauthorizedError("Fehlende Installations-Signatur")
    installation = await session.get(Installation, installation_id)
    if installation is None:
        raise UnauthorizedError("Unbekannte Installation")
    if installation.revoked_at is not None:
        raise UnauthorizedError("Installation wurde widerrufen")
    if not crypto_utils.verify_body(installation.public_key_pem, body, signature):
        raise UnauthorizedError("Ungültige Installations-Signatur")
    if installation.certificate_pem is not None and hub_ca_certificate_pem is not None:
        if not crypto_utils.verify_installation_certificate(
            hub_ca_certificate_pem,
            installation.certificate_pem,
            installation_id=installation.id,
            installation_public_key_pem=installation.public_key_pem,
        ):
            raise UnauthorizedError(
                "Installations-Zertifikat ungültig oder abgelaufen - "
                "Schlüsselrotation stellt ein neues aus"
            )
    return installation


def is_version_compatible(a: Installation, b: Installation) -> bool:
    """Bidirectional check (7.4 "version compatibility"): ``b`` must meet at
    least ``a``'s stated minimum requirement and vice versa. Deliberately a
    simple ``(major, minor)`` numeric scheme instead of a SemVer library.
    `parse_version` can no longer fail here (P13-S3: the format is already
    validated at registration, see `schemas.py`)."""
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
    """``handover_id`` comes from the sending installation itself (see
    `schemas.HandoverCreate`), not generated by the hub - see ADR 0028
    "self-loopback"."""
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
    """Retry-aware version (P20-S5, ADR 0081): a failure no longer leads
    immediately to ``delivery_failed``, but to ``pending_retry`` with
    full-jitter backoff, as long as ``max_attempts`` hasn't been reached yet
    - same pattern as `ocr_service.repository.record_failure` /
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
    """MUST run before a new delivery attempt - otherwise
    `mark_handover_delivered` keeps counting from the already-exhausted
    attempts count (see ADR 0080 "Consequences" for the bug found in
    ocr-service/rendering-service that established this pattern)."""
    handover.attempts = 0
    handover.next_retry_at = None
    await session.flush()


async def mark_handover_result_delivered(
    session: AsyncSession, handover: Handover, *, success: bool
) -> None:
    handover.status = "completed" if success else "result_delivery_failed"
    handover.completed_at = datetime.now(UTC)
    await session.flush()
