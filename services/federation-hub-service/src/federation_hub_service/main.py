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
    """`request.body()` + manuelles `model_validate_json()` (statt eines
    typisierten FastAPI-Body-Parameters) ist nötig, damit die Endpunkte unten
    exakt die rohen, signierten Bytes verifizieren können (P13-S4, ADR 0039) -
    FastAPI wandelt einen so **manuell** ausgelösten `pydantic.ValidationError`
    aber anders als bei einem automatischen Body-Parameter NICHT von selbst in
    einen `422` um (nur seine eigene `RequestValidationError`), daher hier
    explizit gefangen."""
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
        # Ad-hoc-Schema-Erweiterung (kein Alembic in dieser frühen Phase, siehe
        # CONTRIBUTING.md): `create_all` legt fehlende Tabellen an, ändert aber
        # keine bestehenden - `revoked_at`/`revoked_reason` kamen erst in
        # P13-S4 dazu (ADR 0039). `api_key_hash` wird im selben Schritt
        # entfernt statt bewusst zurückgestellt (anders als sonst üblich) -
        # das alte API-Key-Modell wird hier vollständig durch die
        # signaturbasierte Authentisierung ersetzt, nicht schrittweise über
        # mehrere Versionen hinweg abgelöst (kein Rolling-Update-Szenario
        # zwischen alt/neu zu berücksichtigen), und eine verbliebene
        # NOT-NULL-Spalte ohne Server-Default würde jeden neuen Insert
        # brechen, der sie (zu Recht) nicht mehr befüllt.
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
        # Post-Roadmap Phase 20 Session 5 (ADR 0081): Retry/Backoff für die
        # Handover-Erstzustellung, analog zu den vier anderen Resilienz-Stellen
        # dieser Phase.
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

    # Post-Roadmap Phase 20 Session 5 (ADR 0081): der Ende-zu-Ende
    # verschlüsselte Payload wird bewusst NIE in der `handover`-Tabelle
    # persistiert (siehe `models.Handover`-Docstring, ADR 0028) - ein
    # zwischenzeitlich per Retry erneut zuzustellender Payload lebt daher nur
    # FLÜCHTIG in diesem Prozessspeicher-Dict (keyed by handover_id). Ein
    # Neustart des Hub während eines offenen Retry-Fensters verliert damit
    # bewusst die Möglichkeit zur automatischen Nachzustellung - dokumentiert,
    # nicht stillschweigend umgangen (siehe `docs/services/
    # federation-hub-service.md` "Offene Punkte").
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
    """Installationen rufen dies **einmalig** beim ersten Registrieren ab und
    cachen den Schlüssel lokal (Trust-on-First-Use, siehe ADR 0028) - damit
    verifizieren sie jede spätere, vom Hub signierte Zustellung
    (``X-Federation-Hub-Signature``)."""
    return PublicKeyOut(public_key_pem=app.state.hub_public_key_pem.decode("utf-8"))


@app.post("/installations", response_model=InstallationOut, status_code=status.HTTP_201_CREATED)
async def register_installation(
    request: Request,
    x_installation_signature: str = Header(default="", alias="X-Installation-Signature"),
    session: AsyncSession = Depends(get_session),
) -> Installation:
    """Seit P13-S4 (ADR 0039) kryptografisch statt über ein geteiltes
    API-Key-Geheimnis abgesichert - `payload.public_key_pem` dient zugleich
    als Identität, `X-Installation-Signature` beweist den Besitz des
    zugehörigen privaten Schlüssels. Liest den rohen Body statt eines
    typisierten Parameters, damit exakt diese Bytes (nicht ein separat neu
    serialisiertes Objekt) verifiziert werden - gleiches Prinzip wie
    `workflow_service.main.federation_inbound`, nur in umgekehrter Richtung."""
    body = await request.body()
    payload = _parse_body(InstallationRegister, body)
    try:
        installation = await repository.register_or_update_installation(
            session, payload, raw_body=body, presented_signature=x_installation_signature
        )
    except repository.UnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    await session.commit()
    return installation


@app.get("/installations", response_model=list[InstallationOut])
async def list_installations(session: AsyncSession = Depends(get_session)) -> list[InstallationOut]:
    """Das Adressbuch ist bewusst ungegated lesbar (7.4 zufolge braucht ein
    Prozess-Designer nur die öffentlichen Kennungen/Anzeigenamen, um ein
    Ziel auszuwählen) - analog zu `registry-service`s offenem `GET /instances`."""
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
    """Kontrollierte Schlüsselrotation (P13-S4, ADR 0039) - der Request-Body
    (``{"new_public_key_pem": ...}``) muss mit dem noch **aktuellen**
    privaten Schlüssel signiert sein, siehe `repository.rotate_installation_key`."""
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
    await session.commit()
    return installation


@app.post("/installations/{installation_id}/revoke", response_model=InstallationOut)
async def revoke_installation(
    installation_id: str,
    payload: RevokeRequest,
    authorization: str = Header(default="", alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> Installation:
    """Betreiber-Aktion (P13-S4, ADR 0039 "Revocation") - gegated über
    `settings.hub_operator_key`, nicht über die Signatur der betroffenen
    Installation (die könnte kompromittiert sein, siehe `repository.
    revoke_installation`). Ohne konfigurierten `hub_operator_key` vollständig
    gesperrt (`403`) - ein Hub-Betreiber muss Revocation bewusst aktivieren."""
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


async def _run_retry_tick(session_factory, pending_payloads: dict[str, dict]) -> None:
    """Ein einzelner Durchlauf der fälligen Handover-Wiederholungsversuche -
    ausgelagert aus `_handover_retry_poll_loop`, damit ein Tick isoliert
    testbar ist (gleiches Muster wie notification-service's `_run_retry_tick`,
    ADR 0079)."""
    async with session_factory() as session:
        due = await repository.list_due_for_retry(session)
    for stale in due:
        async with session_factory() as session:
            handover = await session.get(Handover, stale.id)
            if handover is None or handover.status != "pending_retry":
                continue  # zwischenzeitlich anders behandelt (z. B. manueller Retry)
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
            # NUR bei Erfolg entfernen - bleibt auch nach Erschöpfung
            # (`delivery_failed`) im Cache, sonst wäre der Cache-Eintrag exakt
            # in dem Moment weg, in dem `POST .../retry` ihn erstmals nutzen
            # könnte (beide Übergänge passieren im selben Tick).
            if delivered:
                pending_payloads.pop(handover.id, None)
            await session.commit()


async def _handover_retry_poll_loop(session_factory, pending_payloads: dict[str, dict]) -> None:
    """Wiederholt fehlgeschlagene Handover-Erstzustellungen (Post-Roadmap
    Phase 20 Session 5, ADR 0081) - der erste Zustellversuch bleibt bewusst
    synchron in `POST /handovers` (schnelle Antwort im Regelfall), nur die
    WIEDERHOLUNG läuft asynchron in diesem eigenen Poll-Loop. Gleiches Idiom
    wie notification-service's `_notification_retry_poll_loop` (ADR 0079)."""
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
            session, installation_id=installation_id, body=body, signature=signature
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
    # Muss vor der Zustellung committet sein: die Zielinstallation kann beim
    # Verarbeiten von `/federation/inbound` synchron in denselben Hub
    # zurückrufen (z. B. ein `federated_return`-Task, der sofort
    # `POST /handovers/{id}/result` aufruft, siehe ADR 0028 "Selbst-Loopback").
    # Dieser verschachtelte Aufruf läuft in einer eigenen DB-Transaktion und
    # sähe die Handover-Zeile sonst noch nicht (Postgres-Transaktionsisolation) -
    # ein `GET`/`POST .../result` auf einen erst noch uncommitteten Handover
    # schlägt sonst reproduzierbar mit 404 fehl.
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
        # Bleibt auch nach Erschöpfung (`delivery_failed`) im Cache - ein
        # manueller `POST .../retry` braucht ihn gerade dann. Nur ein
        # ERFOLGREICHER Versuch (hier oder später im Poll-Loop/Retry-Endpunkt)
        # entfernt den Eintrag wieder. Siehe `lifespan`s Kommentar zur bewusst
        # flüchtigen Natur dieses Cache (ADR 0028, ADR 0081).
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
    """Manueller Neustart eines endgültig fehlgeschlagenen Handover
    (Post-Roadmap Phase 20 Session 5, ADR 0081) - nur für `delivery_failed`
    sinnvoll (409 sonst); unternimmt sofort einen neuen synchronen
    Zustellversuch statt auf den nächsten Poll-Tick zu warten, gleiches Muster
    wie ocr-/rendering-service (ADR 0080). Setzt `attempts`/`next_retry_at`
    zwingend VOR dem erneuten Versuch zurück (`repository.reset_for_retry`).
    Funktioniert nur, solange der verschlüsselte Payload noch im
    Prozessspeicher des Hub liegt - nach einem Neustart während eines offenen
    Retry-Fensters ist er unwiederbringlich verloren (bewusste Konsequenz aus
    "kein Payload wird je persistiert", ADR 0028); die Absenderinstallation
    muss in diesem Fall einen neuen Handover mit neuer handover_id einreichen."""
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
    # Nur bei Erfolg entfernen - bleibt bei erneutem Fehlschlag im Cache, damit
    # ein weiterer manueller Retry (oder ein zwischenzeitlich wieder
    # aufgenommener Poll-Loop, falls attempts noch nicht erschöpft war) ihn
    # weiterhin nutzen kann.
    if delivered:
        app.state.pending_handover_payloads.pop(handover_id, None)
    await session.commit()
    return handover
