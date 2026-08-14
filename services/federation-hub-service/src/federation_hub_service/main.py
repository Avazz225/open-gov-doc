import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from federation_hub_service import repository
from federation_hub_service.crypto_utils import sign_body
from federation_hub_service.models import Base, Handover, Installation
from federation_hub_service.schemas import (
    CaCertificateOut,
    HandoverCreate,
    HandoverOut,
    HandoverResultSubmit,
    InstallationOut,
    InstallationRegister,
    PublicKeyOut,
    RevokeRequest,
    RotateKeyRequest,
)
from federation_hub_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


def _parse_body(model: type[BaseModel], body: bytes):
    """`request.body()` + manual `model_validate_json()` (instead of a typed
    FastAPI body parameter) is necessary so that the endpoints below can
    verify exactly the raw, signed bytes (P13-S4, ADR 0039) - however, unlike
    with an automatic body parameter, FastAPI does NOT automatically convert
    a `pydantic.ValidationError` raised this **manually** into a `422` (only
    its own `RequestValidationError`), so it is caught explicitly here."""
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS federation"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc schema extension (no Alembic in this early phase, see
        # CONTRIBUTING.md): `create_all` creates missing tables but does not
        # alter existing ones - `revoked_at`/`revoked_reason` were only added
        # in P13-S4 (ADR 0039). `api_key_hash` is removed in the same step
        # instead of being deliberately deferred (unlike the usual approach)
        # - the old API-key model is fully replaced here by
        # signature-based authentication, not phased out gradually across
        # multiple versions (no rolling-update scenario between old/new to
        # account for), and a remaining NOT-NULL column without a server
        # default would break every new insert that (rightly) no longer
        # populates it.
        await conn.execute(
            text(
                "ALTER TABLE federation.installation "
                "ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text("ALTER TABLE federation.installation ADD COLUMN IF NOT EXISTS revoked_reason TEXT")
        )
        await conn.execute(
            text("ALTER TABLE federation.installation DROP COLUMN IF EXISTS api_key_hash")
        )
        # Post-Roadmap Phase 20 Session 5 (ADR 0081): retry/backoff for the
        # initial handover delivery, analogous to the four other resilience
        # spots of this phase.
        await conn.execute(
            text(
                "ALTER TABLE federation.handover "
                "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE federation.handover "
                "ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ"
            )
        )
        # Post-Roadmap Phase 21 Session 2 (ADR 0085): certificate layer on top
        # of the existing signature check, see models.py docstrings.
        await conn.execute(
            text(
                "ALTER TABLE federation.hub_identity "
                "ADD COLUMN IF NOT EXISTS ca_certificate_pem BYTEA"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE federation.installation "
                "ADD COLUMN IF NOT EXISTS certificate_pem TEXT"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE federation.installation "
                "ADD COLUMN IF NOT EXISTS certificate_not_after TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        identity = await repository.get_or_create_hub_identity(session)
        # Backfill migration (ADR 0085): installations registered before this
        # session don't yet have a certificate - caught up here once so that
        # `authenticate_signed_request`'s grandfathering exception isn't
        # relied upon permanently in practice.
        installations_without_certificate = await repository.list_installations_without_certificate(
            session
        )
        for installation in installations_without_certificate:
            await repository.issue_or_renew_installation_certificate(
                session,
                installation,
                ca_certificate_pem=identity.ca_certificate_pem,
                ca_private_key_pem=identity.private_key_pem,
            )
        await session.commit()
        app.state.hub_private_key_pem = identity.private_key_pem
        app.state.hub_public_key_pem = identity.public_key_pem
        app.state.hub_ca_certificate_pem = identity.ca_certificate_pem

    # A single shared client for all outgoing deliveries to installation
    # callback URLs - swappable in tests (`app.state.http_client =
    # httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))`), so that a
    # handover round trip can be tested without a real network.
    app.state.http_client = httpx.AsyncClient(timeout=15.0)

    # Post-Roadmap Phase 20 Session 5 (ADR 0081): the end-to-end encrypted
    # payload is deliberately NEVER persisted in the `handover` table (see
    # `models.Handover` docstring, ADR 0028) - a payload that still needs to
    # be redelivered via retry therefore only lives EPHEMERALLY in this
    # in-process memory dict (keyed by handover_id). A restart of the hub
    # during an open retry window therefore deliberately loses the ability
    # to automatically redeliver - documented, not silently worked around
    # (see `docs/services/federation-hub-service.md` "Open Points").
    app.state.pending_handover_payloads = {}
    retry_poll_task = asyncio.create_task(
        _handover_retry_poll_loop(app.state.session_factory, app.state.pending_handover_payloads)
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    retry_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await retry_poll_task
    await app.state.http_client.aclose()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/public-key", response_model=PublicKeyOut)
def get_public_key() -> PublicKeyOut:
    """Installations fetch this **once** during initial registration and
    cache the key locally (trust-on-first-use, see ADR 0028) - they use it to
    verify every subsequent delivery signed by the hub
    (``X-Federation-Hub-Signature``)."""
    return PublicKeyOut(public_key_pem=app.state.hub_public_key_pem.decode("utf-8"))


@app.get("/ca-certificate", response_model=CaCertificateOut)
def get_ca_certificate() -> CaCertificateOut:
    """Self-signed root CA certificate of the hub (Post-Roadmap Phase 21
    Session 2, ADR 0085) - installations can fetch and pin it on first
    contact, analogous to `GET /public-key` (trust-on-first-use,
    certificate-pinning equivalent), to locally validate installation
    certificates issued later by the hub. Purely informational for the hub
    itself - the actual certificate check happens server-side in
    `authenticate_signed_request`, not here."""
    return CaCertificateOut(ca_certificate_pem=app.state.hub_ca_certificate_pem.decode("utf-8"))


@app.post("/installations", response_model=InstallationOut, status_code=status.HTTP_201_CREATED)
async def register_installation(
    request: Request,
    x_installation_signature: str = Header(default="", alias="X-Installation-Signature"),
    session: AsyncSession = Depends(get_session),
) -> Installation:
    """Since P13-S4 (ADR 0039) secured cryptographically instead of via a
    shared API-key secret - `payload.public_key_pem` simultaneously serves as
    identity, `X-Installation-Signature` proves possession of the
    corresponding private key. Reads the raw body instead of a typed
    parameter so that exactly these bytes (not a separately re-serialized
    object) are verified - same principle as
    `workflow_service.main.federation_inbound`, just in the reverse
    direction."""
    body = await request.body()
    payload = _parse_body(InstallationRegister, body)
    try:
        installation = await repository.register_or_update_installation(
            session, payload, raw_body=body, presented_signature=x_installation_signature
        )
    except repository.UnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    # Post-Roadmap Phase 21 Session 2 (ADR 0085): reissued both on initial
    # creation and on a regular re-registration (`public_key_pem` itself
    # doesn't change on a re-registration, see
    # `register_or_update_installation`, but a fresh certificate doesn't
    # hurt and keeps the validity period consistently up to date).
    await repository.issue_or_renew_installation_certificate(
        session,
        installation,
        ca_certificate_pem=app.state.hub_ca_certificate_pem,
        ca_private_key_pem=app.state.hub_private_key_pem,
    )
    await session.commit()
    return installation


@app.get("/installations", response_model=list[InstallationOut])
async def list_installations(session: AsyncSession = Depends(get_session)) -> list[InstallationOut]:
    """The address book is deliberately readable without gating (per 7.4, a
    process designer only needs the public identifiers/display names to
    select a target) - analogous to `registry-service`'s open
    `GET /instances`."""
    return await repository.list_installations(session)


@app.delete("/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_installation(
    installation_id: str,
    x_installation_signature: str = Header(default="", alias="X-Installation-Signature"),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await repository.deregister_installation(
            session, installation_id, presented_signature=x_installation_signature
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.UnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    await session.commit()


@app.post("/installations/{installation_id}/rotate-key", response_model=InstallationOut)
async def rotate_installation_key(
    installation_id: str,
    request: Request,
    x_installation_signature: str = Header(default="", alias="X-Installation-Signature"),
    session: AsyncSession = Depends(get_session),
) -> Installation:
    """Controlled key rotation (P13-S4, ADR 0039) - the request body
    (``{"new_public_key_pem": ...}``) must be signed with the still
    **current** private key, see `repository.rotate_installation_key`."""
    body = await request.body()
    payload = _parse_body(RotateKeyRequest, body)
    try:
        installation = await repository.rotate_installation_key(
            session,
            installation_id,
            raw_body=body,
            new_public_key_pem=payload.new_public_key_pem,
            presented_signature=x_installation_signature,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.UnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    # Post-Roadmap Phase 21 Session 2 (ADR 0085): MUST be reissued - the
    # previous certificate bound the now-replaced old key.
    await repository.issue_or_renew_installation_certificate(
        session,
        installation,
        ca_certificate_pem=app.state.hub_ca_certificate_pem,
        ca_private_key_pem=app.state.hub_private_key_pem,
    )
    await session.commit()
    return installation


@app.post("/installations/{installation_id}/revoke", response_model=InstallationOut)
async def revoke_installation(
    installation_id: str,
    payload: RevokeRequest,
    authorization: str = Header(default="", alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> Installation:
    """Operator action (P13-S4, ADR 0039 "Revocation") - gated via
    `settings.hub_operator_key`, not via the signature of the affected
    installation (which could be compromised, see `repository.
    revoke_installation`). Fully locked (`403`) without a configured
    `hub_operator_key` - a hub operator must deliberately enable
    revocation."""
    if not settings.hub_operator_key or authorization != f"Bearer {settings.hub_operator_key}":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlender oder ungültiger Hub-Operator-Schlüssel",
        )
    try:
        installation = await repository.revoke_installation(
            session, installation_id, reason=payload.reason
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return installation


async def _deliver(url: str, body: dict) -> bool:
    """Sends ``body`` as a JSON request signed by the hub to ``url`` - the
    exact, sent bytes are signed (not a separately re-serialized object), so
    the receiving side can verify over exactly these bytes. Network
    errors/non-2xx responses count as a failed delivery but do not raise an
    exception - the caller decides the handover status based on the return
    value."""
    raw = json.dumps(body).encode("utf-8")
    signature = sign_body(app.state.hub_private_key_pem, raw)
    try:
        response = await app.state.http_client.post(
            url,
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Federation-Hub-Signature": signature,
            },
        )
        return response.is_success
    except httpx.HTTPError:
        logger.warning("federation_delivery_failed", extra={"url": url})
        return False


async def _run_retry_tick(session_factory, pending_payloads: dict[str, dict]) -> None:
    """A single pass over the due handover retry attempts - factored out of
    `_handover_retry_poll_loop` so that a tick is independently testable
    (same pattern as notification-service's `_run_retry_tick`, ADR 0079)."""
    async with session_factory() as session:
        due = await repository.list_due_for_retry(session)
    for stale in due:
        async with session_factory() as session:
            handover = await session.get(Handover, stale.id)
            if handover is None or handover.status != "pending_retry":
                continue  # handled differently in the meantime (e.g. manual retry)
            cached = pending_payloads.get(handover.id)
            if cached is None:
                logger.warning(
                    "federation_handover_retry_payload_lost", extra={"handover_id": handover.id}
                )
                handover.status = "delivery_failed"
                handover.next_retry_at = None
                await session.commit()
                continue
            to_installation = await session.get(Installation, handover.to_installation_id)
            delivered = False
            if to_installation is not None:
                delivered = await _deliver(
                    to_installation.callback_base_url.rstrip("/") + "/federation/inbound", cached
                )
            await repository.mark_handover_delivered(
                session,
                handover,
                success=delivered,
                max_attempts=settings.max_handover_delivery_attempts,
            )
            # ONLY remove on success - stays in the cache even after
            # exhaustion (`delivery_failed`), otherwise the cache entry would
            # be gone exactly at the moment `POST .../retry` could first use
            # it (both transitions happen in the same tick).
            if delivered:
                pending_payloads.pop(handover.id, None)
            await session.commit()


async def _handover_retry_poll_loop(session_factory, pending_payloads: dict[str, dict]) -> None:
    """Retries failed initial handover deliveries (Post-Roadmap Phase 20
    Session 5, ADR 0081) - the first delivery attempt deliberately stays
    synchronous in `POST /handovers` (fast response in the normal case), only
    the RETRY runs asynchronously in this dedicated poll loop. Same idiom as
    notification-service's `_notification_retry_poll_loop` (ADR 0079)."""
    while True:
        try:
            await _run_retry_tick(session_factory, pending_payloads)
        except Exception:
            logger.exception(
                "Federation-Handover-Retry-Poll-Tick fehlgeschlagen - "
                "wird beim naechsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.handover_retry_poll_interval_seconds)


async def _authenticate(
    session: AsyncSession, *, installation_id: str, body: bytes, signature: str
) -> Installation:
    try:
        return await repository.authenticate_signed_request(
            session,
            installation_id=installation_id,
            body=body,
            signature=signature,
            hub_ca_certificate_pem=app.state.hub_ca_certificate_pem,
        )
    except repository.UnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@app.post("/handovers", response_model=HandoverOut, status_code=status.HTTP_201_CREATED)
async def create_handover(
    request: Request,
    x_installation_id: str = Header(default="", alias="X-Installation-Id"),
    x_installation_signature: str = Header(default="", alias="X-Installation-Signature"),
    session: AsyncSession = Depends(get_session),
) -> HandoverOut:
    body = await request.body()
    from_installation = await _authenticate(
        session, installation_id=x_installation_id, body=body, signature=x_installation_signature
    )
    payload = _parse_body(HandoverCreate, body)
    to_installation = await session.get(Installation, payload.to_installation_id)
    if to_installation is None:
        raise HTTPException(
            status_code=404, detail=f"to_installation_id {payload.to_installation_id!r} unbekannt"
        )
    if to_installation.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"to_installation_id {payload.to_installation_id!r} wurde widerrufen",
        )
    if not repository.is_version_compatible(from_installation, to_installation):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Versionen nicht kompatibel: {from_installation.id!r} "
                f"({from_installation.version}, min. Peer "
                f"{from_installation.min_compatible_peer_version}) <-> "
                f"{to_installation.id!r} ({to_installation.version}, "
                f"min. Peer {to_installation.min_compatible_peer_version})"
            ),
        )

    handover = await repository.create_handover(
        session,
        handover_id=payload.handover_id,
        from_installation_id=from_installation.id,
        to_installation_id=to_installation.id,
        process_type=payload.process_type,
    )
    # Must be committed before delivery: the target installation can call
    # back synchronously into the same hub while processing
    # `/federation/inbound` (e.g. a `federated_return` task that immediately
    # calls `POST /handovers/{id}/result`, see ADR 0028 "self-loopback").
    # This nested call runs in its own DB transaction and would otherwise not
    # yet see the handover row (Postgres transaction isolation) - a
    # `GET`/`POST .../result` on a handover that hasn't been committed yet
    # would otherwise reproducibly fail with 404.
    await session.commit()

    delivery_body = {
        "handover_id": handover.id,
        "from_installation_id": from_installation.id,
        "process_type": payload.process_type,
        "encrypted_payload": payload.encrypted_payload,
    }
    delivered = await _deliver(
        to_installation.callback_base_url.rstrip("/") + "/federation/inbound", delivery_body
    )
    await repository.mark_handover_delivered(
        session, handover, success=delivered, max_attempts=settings.max_handover_delivery_attempts
    )
    if not delivered:
        # Stays in the cache even after exhaustion (`delivery_failed`) - a
        # manual `POST .../retry` needs it precisely then. Only a
        # SUCCESSFUL attempt (here or later in the poll loop/retry endpoint)
        # removes the entry again. See `lifespan`'s comment on the
        # deliberately ephemeral nature of this cache (ADR 0028, ADR 0081).
        app.state.pending_handover_payloads[handover.id] = delivery_body
    await session.commit()
    return handover


@app.post("/handovers/{handover_id}/result", response_model=HandoverOut)
async def submit_handover_result(
    handover_id: str,
    request: Request,
    x_installation_id: str = Header(default="", alias="X-Installation-Id"),
    x_installation_signature: str = Header(default="", alias="X-Installation-Signature"),
    session: AsyncSession = Depends(get_session),
) -> HandoverOut:
    body = await request.body()
    caller = await _authenticate(
        session, installation_id=x_installation_id, body=body, signature=x_installation_signature
    )
    payload = _parse_body(HandoverResultSubmit, body)
    try:
        handover = await repository.get_handover(session, handover_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if handover.to_installation_id != caller.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur die Zielinstallation dieses Handover darf ein Ergebnis zurückmelden",
        )

    origin = await session.get(Installation, handover.from_installation_id)
    delivered = origin is not None and await _deliver(
        origin.callback_base_url.rstrip("/") + "/federation/inbound-result",
        {
            "handover_id": handover.id,
            "outcome": payload.outcome,
            "encrypted_result": payload.encrypted_result,
        },
    )
    await repository.mark_handover_result_delivered(session, handover, success=delivered)
    await session.commit()
    return handover


@app.get("/handovers/{handover_id}", response_model=HandoverOut)
async def get_handover(
    handover_id: str, session: AsyncSession = Depends(get_session)
) -> HandoverOut:
    try:
        return await repository.get_handover(session, handover_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/handovers/{handover_id}/retry", response_model=HandoverOut)
async def retry_handover(
    handover_id: str, session: AsyncSession = Depends(get_session)
) -> HandoverOut:
    """Manual restart of a permanently failed handover (Post-Roadmap Phase 20
    Session 5, ADR 0081) - only meaningful for `delivery_failed` (409
    otherwise); makes a new synchronous delivery attempt immediately instead
    of waiting for the next poll tick, same pattern as ocr-/rendering-service
    (ADR 0080). MUST reset `attempts`/`next_retry_at` BEFORE the new attempt
    (`repository.reset_for_retry`). Only works as long as the encrypted
    payload is still in the hub's process memory - after a restart during an
    open retry window it is irrecoverably lost (deliberate consequence of "no
    payload is ever persisted", ADR 0028); in that case the sending
    installation must submit a new handover with a new handover_id."""
    try:
        handover = await repository.get_handover(session, handover_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if handover.status != "delivery_failed":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Handover hat Status {handover.status!r}, nur 'delivery_failed' "
                "kann erneut versucht werden"
            ),
        )
    cached = app.state.pending_handover_payloads.get(handover_id)
    if cached is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Verschlüsselter Payload ist nicht mehr im Hub-Speicher vorhanden "
                "(z. B. nach einem Neustart) - die Absenderinstallation muss einen "
                "neuen Handover mit neuer handover_id einreichen"
            ),
        )
    await repository.reset_for_retry(session, handover)
    await session.commit()

    to_installation = await session.get(Installation, handover.to_installation_id)
    delivered = False
    if to_installation is not None:
        delivered = await _deliver(
            to_installation.callback_base_url.rstrip("/") + "/federation/inbound", cached
        )
    await repository.mark_handover_delivered(
        session, handover, success=delivered, max_attempts=settings.max_handover_delivery_attempts
    )
    # Only remove on success - stays in the cache on renewed failure, so a
    # further manual retry (or a poll loop resumed in the meantime, if
    # attempts hadn't been exhausted yet) can still use it.
    if delivered:
        app.state.pending_handover_payloads.pop(handover_id, None)
    await session.commit()
    return handover
