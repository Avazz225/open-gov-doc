import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_management_service import repository
from fleet_management_service.agent_client import AgentError, FleetAgentClient
from fleet_management_service.models import Base, ManagedInstallation
from fleet_management_service.schemas import (
    InstallationStatusOut,
    LicenseUploadRequest,
    ManagedInstallationCreate,
    ManagedInstallationCreateOut,
    ManagedInstallationOut,
    ProvisionRequest,
)
from fleet_management_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

# Für Tests austauschbar (`app.state.agent_transport = httpx.MockTransport(...)`),
# damit ein Statusabruf/eine Provisionierung ohne echtes Netzwerk gegen einen
# In-Prozess-Stub der Ziel-Installation läuft (gleiches Muster wie
# `federation-hub-service`s `app.state.http_client`-Austausch in Tests).
_AGENT_TRANSPORT_STATE_KEY = "agent_transport"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS fleet"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.agent_transport = None

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


def _agent_client(installation: ManagedInstallation) -> FleetAgentClient:
    return FleetAgentClient(
        gateway_base_url=installation.gateway_base_url,
        fleet_agent_api_key=installation.fleet_agent_api_key,
        timeout=settings.agent_request_timeout_seconds,
        transport=app.state.agent_transport,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post(
    "/installations",
    response_model=ManagedInstallationCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_installation(
    payload: ManagedInstallationCreate, session: AsyncSession = Depends(get_session)
) -> ManagedInstallationCreateOut:
    installation, api_key = await repository.create_managed_installation(session, payload)
    await session.commit()
    return ManagedInstallationCreateOut(
        id=installation.id,
        display_name=installation.display_name,
        gateway_base_url=installation.gateway_base_url,
        created_at=installation.created_at,
        updated_at=installation.updated_at,
        fleet_agent_api_key=api_key,
    )


@app.get("/installations", response_model=list[ManagedInstallationOut])
async def list_installations(
    session: AsyncSession = Depends(get_session),
) -> list[ManagedInstallation]:
    """Bewusst ohne `fleet_agent_api_key` im Response-Model - der Klartext-
    Schlüssel wird nur einmal bei der Anlage zurückgegeben (siehe oben)."""
    return await repository.list_managed_installations(session)


@app.delete("/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_installation(
    installation_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_managed_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


async def _fetch_status(installation: ManagedInstallation) -> InstallationStatusOut:
    client = _agent_client(installation)
    try:
        identity = await client.get_installation_identity()
        license_status = await client.get_license_status()
        return InstallationStatusOut(
            id=installation.id,
            display_name=installation.display_name,
            reachable=True,
            installation_id=identity.get("id"),
            installation_display_name=identity.get("display_name"),
            license_status=license_status,
        )
    except Exception as exc:  # noqa: BLE001 - eine nicht erreichbare Installation
        # darf die Übersicht der übrigen nicht verhindern (siehe schemas.py).
        logger.warning(
            "fleet_agent_status_unreachable",
            extra={"installation_id": installation.id, "error": str(exc)},
        )
        return InstallationStatusOut(
            id=installation.id,
            display_name=installation.display_name,
            reachable=False,
            error=str(exc),
        )
    finally:
        await client.close()


@app.get("/installations/{installation_id}/status", response_model=InstallationStatusOut)
async def get_installation_status(
    installation_id: str, session: AsyncSession = Depends(get_session)
) -> InstallationStatusOut:
    try:
        installation = await repository.get_managed_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _fetch_status(installation)


@app.get("/installations/status", response_model=list[InstallationStatusOut])
async def list_installation_statuses(
    session: AsyncSession = Depends(get_session),
) -> list[InstallationStatusOut]:
    """3a: "grundlegende Health-Übersicht" über die gesamte Flotte - parallel
    abgefragt (`asyncio.gather`), damit eine einzelne langsame/nicht
    erreichbare Installation die Antwortzeit für die übrigen nicht
    dominiert."""
    installations = await repository.list_managed_installations(session)
    return list(await asyncio.gather(*(_fetch_status(i) for i in installations)))


@app.post("/installations/{installation_id}/license")
async def push_license(
    installation_id: str,
    payload: LicenseUploadRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """3a: "Lizenzvergabe/-verlängerung" - reicht das Lizenztoken über den
    Gateway der Zielinstallation an deren `license-service` weiter, siehe
    `agent_client.FleetAgentClient.upload_license`."""
    try:
        installation = await repository.get_managed_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    client = _agent_client(installation)
    try:
        return await client.upload_license(payload.license_token)
    except AgentError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await client.close()


@app.post("/installations/{installation_id}/provision")
async def provision_installation(
    installation_id: str,
    payload: ProvisionRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """3a: "zentrales Provisioning neuer Installationen aus einer
    Konfigurationsvorlage heraus" - `payload.config_document` ist ein
    reguläres 7.3-Konfigurationsdokument (z. B. der Export einer
    Referenzinstallation, oder künftig ein kuratiertes Paket aus Phase 17),
    dieser Service kuratiert selbst keine Vorlagen-Bibliothek."""
    try:
        installation = await repository.get_managed_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    client = _agent_client(installation)
    try:
        return await client.provision_config(payload.config_document, categories=payload.categories)
    except AgentError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await client.close()
