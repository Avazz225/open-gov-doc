import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from federation_hub_service import repository
from federation_hub_service.crypto_utils import sign_body
from federation_hub_service.models import Base, Installation
from federation_hub_service.schemas import (
    HandoverCreate,
    HandoverOut,
    HandoverResultSubmit,
    InstallationOut,
    InstallationRegister,
    InstallationRegisterOut,
    PublicKeyOut,
)
from federation_hub_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


def _extract_bearer(authorization: str) -> str | None:
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization[len("bearer ") :].strip()
    return token or None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS federation"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        identity = await repository.get_or_create_hub_identity(session)
        await session.commit()
        app.state.hub_private_key_pem = identity.private_key_pem
        app.state.hub_public_key_pem = identity.public_key_pem

    # Ein einzelner geteilter Client für alle ausgehenden Zustellungen an
    # Installations-Callback-URLs - austauschbar in Tests (`app.state.http_client
    # = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))`), damit ein
    # Handover-Rundlauf ohne echtes Netzwerk getestet werden kann.
    app.state.http_client = httpx.AsyncClient(timeout=15.0)

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

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
    """Installationen rufen dies **einmalig** beim ersten Registrieren ab und
    cachen den Schlüssel lokal (Trust-on-First-Use, siehe ADR 0028) - damit
    verifizieren sie jede spätere, vom Hub signierte Zustellung
    (``X-Federation-Hub-Signature``)."""
    return PublicKeyOut(public_key_pem=app.state.hub_public_key_pem.decode("utf-8"))


@app.post(
    "/installations", response_model=InstallationRegisterOut, status_code=status.HTTP_201_CREATED
)
async def register_installation(
    payload: InstallationRegister,
    authorization: str = Header(default="", alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> InstallationRegisterOut:
    try:
        installation, api_key = await repository.register_or_update_installation(
            session, payload, presented_api_key=_extract_bearer(authorization)
        )
    except repository.UnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    await session.commit()
    return InstallationRegisterOut(id=installation.id, api_key=api_key)


@app.get("/installations", response_model=list[InstallationOut])
async def list_installations(session: AsyncSession = Depends(get_session)) -> list[InstallationOut]:
    """Das Adressbuch ist bewusst ungegated lesbar (7.4 zufolge braucht ein
    Prozess-Designer nur die öffentlichen Kennungen/Anzeigenamen, um ein
    Ziel auszuwählen) - analog zu `registry-service`s offenem `GET /instances`."""
    return await repository.list_installations(session)


@app.delete("/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_installation(
    installation_id: str,
    authorization: str = Header(default="", alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await repository.deregister_installation(
            session, installation_id, presented_api_key=_extract_bearer(authorization)
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.UnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    await session.commit()


async def _authenticate(session: AsyncSession, authorization: str):
    api_key = _extract_bearer(authorization)
    installation = api_key and await repository.get_installation_by_api_key(session, api_key)
    if not installation:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger oder fehlender API-Key"
        )
    return installation


async def _deliver(url: str, body: dict) -> bool:
    """Sendet ``body`` als vom Hub signierten JSON-Request an ``url`` - die
    exakten, gesendeten Bytes werden signiert (nicht ein separat neu
    serialisiertes Objekt), damit die empfangende Seite über genau diese Bytes
    verifizieren kann. Netzwerkfehler/Nicht-2xx-Antworten gelten als
    fehlgeschlagene Zustellung, werfen aber keine Exception - der Aufrufer
    entscheidet anhand des Rückgabewerts, wie der Handover-Status lautet."""
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


@app.post("/handovers", response_model=HandoverOut, status_code=status.HTTP_201_CREATED)
async def create_handover(
    payload: HandoverCreate,
    authorization: str = Header(default="", alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> HandoverOut:
    from_installation = await _authenticate(session, authorization)
    to_installation = await session.get(Installation, payload.to_installation_id)
    if to_installation is None:
        raise HTTPException(
            status_code=404, detail=f"to_installation_id {payload.to_installation_id!r} unbekannt"
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
    # Muss vor der Zustellung committet sein: die Zielinstallation kann beim
    # Verarbeiten von `/federation/inbound` synchron in denselben Hub
    # zurückrufen (z. B. ein `federated_return`-Task, der sofort
    # `POST /handovers/{id}/result` aufruft, siehe ADR 0028 "Selbst-Loopback").
    # Dieser verschachtelte Aufruf läuft in einer eigenen DB-Transaktion und
    # sähe die Handover-Zeile sonst noch nicht (Postgres-Transaktionsisolation) -
    # ein `GET`/`POST .../result` auf einen erst noch uncommitteten Handover
    # schlägt sonst reproduzierbar mit 404 fehl.
    await session.commit()

    delivered = await _deliver(
        to_installation.callback_base_url.rstrip("/") + "/federation/inbound",
        {
            "handover_id": handover.id,
            "from_installation_id": from_installation.id,
            "process_type": payload.process_type,
            "encrypted_payload": payload.encrypted_payload,
        },
    )
    await repository.mark_handover_delivered(session, handover, success=delivered)
    await session.commit()
    return handover


@app.post("/handovers/{handover_id}/result", response_model=HandoverOut)
async def submit_handover_result(
    handover_id: str,
    payload: HandoverResultSubmit,
    authorization: str = Header(default="", alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> HandoverOut:
    caller = await _authenticate(session, authorization)
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
