import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Literal

import httpx
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workflow_service import federation_crypto, repository, spiff_adapter
from workflow_service.federation_client import FederationHubClient
from workflow_service.license_client import LicenseStatusClient
from workflow_service.models import Base, FederationConfig, FederationIdentity
from workflow_service.permission_client import PermissionServiceClient
from workflow_service.schemas import (
    FederationConfigOut,
    FederationConfigUpdate,
    ProcessDefinitionDetailOut,
    ProcessDefinitionOut,
    ProcessInstanceCreate,
    ProcessInstanceOut,
    ReadyTaskOut,
    ReadyTaskWithInstanceOut,
    TaskCompleteRequest,
)
from workflow_service.settings import Settings
from workflow_service.signature_client import SignatureServiceClient

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_SIGNATURE_LEVEL_RANK = {"ses": 0, "aes": 1, "qes": 2}
_FEDERATION_IDENTITY_ID = 1
_FEDERATION_CONFIG_ID = 1
_FEDERATED_TASK_TYPES = ("federated", "federated_return")


# Eigener, austauschbarer synchroner Client (statt der freien `httpx.post()`-
# Funktion) - Tests ersetzen ihn per `monkeypatch` durch einen `httpx.Client` mit
# `httpx.MockTransport`, gleiches Stub-Prinzip wie `federation-hub-service`s
# Tests (dort `httpx.AsyncClient`/`ASGITransport`, hier synchron, siehe unten).
_connector_http_client = httpx.Client()


def _handle_connector_task(extensions: dict[str, str], data: dict) -> dict:
    """Registriert bei `spiff_adapter.ConnectorServiceTask` (7.1 "Auslösen eines
    Connector-Aufrufs", P12-S2) - bewusst der einzige Ort in diesem Service, der
    `httpx` synchron aufruft: SpiffWorkflows `do_engine_steps()` ist durchgehend
    synchron (kein `async`/`await` irgendwo in der Engine), ein Aufruf hier blockiert
    also ohnehin schon den umgebenden `async def`-Request-Handler - konsistent mit
    jeder anderen SpiffWorkflow-Interaktion dieses Service (siehe `spiff_adapter.py`-
    Moduldocstring), keine neue async/sync-Brücke nötig. `serviceUrl` kennt dieser
    Service selbst nicht - komplett generisch, kein Wissen über den aufrufenden
    Service (Migration-Service ist der erste, aber nicht einzig denkbare Nutzer).
    `serviceUrl` unterstützt `{platzhalter}`-Substitution aus den aktuellen
    Prozessdaten (`str.format(**data)`) - so kann z. B. eine pro Instanz
    unterschiedliche `transfer_id` in die URL einfließen, ohne dass die BPMN-
    Datei selbst pro Instanz individuell erzeugt werden müsste."""
    service_url = extensions.get("serviceUrl")
    if not service_url:
        raise RuntimeError("connector_call Service Task ohne serviceUrl-Extension")
    try:
        service_url = service_url.format(**data)
    except (KeyError, IndexError) as exc:
        raise RuntimeError(
            f"serviceUrl {service_url!r} referenziert eine unbekannte Prozessvariable: {exc}"
        ) from exc
    response = _connector_http_client.post(
        service_url, json=data, timeout=settings.connector_call_timeout_seconds
    )
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


spiff_adapter.register_connector_task_handler(_handle_connector_task)


async def _sla_poll_loop(
    session_factory: async_sessionmaker[AsyncSession], permission_client: PermissionServiceClient
) -> None:
    """SLA-Zeitüberwachung (P6-S2, ADR 0020): pollt statt push-basiert zu reagieren,
    da weder SpiffWorkflow noch dieses Projekt einen Hintergrund-Scheduler mitbringen.
    Ein Fehler in einem Tick bricht die Schleife nicht ab, damit ein einzelner defekter
    Blob nicht die SLA-Überwachung aller anderen laufenden Instanzen stoppt.
    Seit P6-S6 zusätzlich: überspringt den Tick während aktivem Wartungsmodus (4.8) -
    "geplante/periodische Jobs werden angehalten"."""
    while True:
        try:
            if await permission_client.is_maintenance_active():
                await asyncio.sleep(settings.sla_poll_interval_seconds)
                continue
            async with session_factory() as session:
                results = await repository.advance_timers(session)
                await session.commit()
            for result in results:
                for fired in result.fired:
                    await publish_event(
                        "workflow.task.escalated",
                        subject=result.instance.id,
                        payload={
                            "process_definition_id": result.instance.process_definition_id,
                            "business_key": result.instance.business_key,
                            "task_name": fired.name,
                            "lane": fired.lane,
                            "escalation_email": fired.data.get("escalation_email"),
                        },
                        actor="system:sla-poll",
                    )
                if result.newly_completed:
                    await publish_event(
                        "workflow.instance.completed",
                        subject=result.instance.id,
                        payload={"business_key": result.instance.business_key},
                        actor="system:sla-poll",
                    )
        except Exception:
            logger.exception(
                "SLA-Poll-Tick fehlgeschlagen - wird beim nächsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.sla_poll_interval_seconds)


async def _get_or_seed_federation_config(session: AsyncSession) -> FederationConfig:
    """7.4/P13-S3: die Versionskompatibilitäts-Erklärung lebt seit P13-S3 in
    dieser DB-Zeile statt direkt in `Settings` - beim allerersten Zugriff aus
    den (weiterhin gültigen) `Settings`-Defaults geseedet, rückwärtskompatibel
    zu allen Installationen, die noch nie eine `PUT /federation/config`
    ausgeführt haben."""
    config = await session.get(FederationConfig, _FEDERATION_CONFIG_ID)
    if config is None:
        config = FederationConfig(
            id=_FEDERATION_CONFIG_ID,
            version=settings.installation_version,
            min_compatible_peer_version=settings.installation_min_compatible_peer_version,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def _ensure_federation_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> FederationHubClient | None:
    """Einmalige Selbstregistrierung am Federation Hub (7.4, P6-S9) - opt-in,
    bleibt `None` ohne konfigurierten `settings.federation_hub_base_url`
    (siehe `docs/services/workflow-service.md` "Federation"). Analog zu
    `maybe_start_registration` aus `dms-registry-client`, aber bewusst kein
    Wiederverwenden dieser Lib: der Hub ist kein interner
    Service-Discovery-Eintrag dieser Installation, sondern ein externer,
    installationsübergreifender Dienst mit eigenem Registrierungs-/
    Auth-Protokoll (API-Key statt Heartbeat, RSA-Schlüsselpaar statt
    Health-Endpoint, siehe `federation_client.py`/`federation_crypto.py`)."""
    if not settings.federation_hub_base_url:
        return None
    client = FederationHubClient(settings.federation_hub_base_url)
    callback_base_url = f"{settings.installation_gateway_base_url}/api/workflow-service"
    async with session_factory() as session:
        config = await _get_or_seed_federation_config(session)
        identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
        if identity is None:
            private_pem, public_pem = federation_crypto.generate_keypair()
            hub_public_key_pem = await client.get_hub_public_key()
            installation_id = str(uuid.uuid4())
            await client.register(
                installation_id=installation_id,
                private_key_pem=private_pem,
                display_name=settings.installation_display_name,
                callback_base_url=callback_base_url,
                public_key_pem=public_pem.decode("utf-8"),
                version=config.version,
                min_compatible_peer_version=config.min_compatible_peer_version,
            )
            identity = FederationIdentity(
                id=_FEDERATION_IDENTITY_ID,
                installation_id=installation_id,
                private_key_pem=private_pem,
                public_key_pem=public_pem,
                hub_public_key_pem=hub_public_key_pem.encode("utf-8"),
                created_at=datetime.now(UTC),
            )
            session.add(identity)
            await session.commit()
        else:
            # Re-Registrierung bei jedem Start (Upsert wie bei der internen
            # Registry) - hält z. B. `callback_base_url`/`version` aktuell,
            # falls sich Settings/`FederationConfig` zwischen Neustarts
            # geändert haben. Ein Fehlschlag (Hub gerade nicht erreichbar)
            # blockiert den eigenen Start nicht - Federation ist ein
            # Zusatznutzen, kein Hard-Dependency dieser Installation.
            try:
                await client.register(
                    installation_id=identity.installation_id,
                    private_key_pem=identity.private_key_pem,
                    display_name=settings.installation_display_name,
                    callback_base_url=callback_base_url,
                    public_key_pem=identity.public_key_pem.decode("utf-8"),
                    version=config.version,
                    min_compatible_peer_version=config.min_compatible_peer_version,
                )
            except httpx.HTTPError:
                logger.warning("federation_hub_reregistration_failed")
    return client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS workflow"))
        await conn.run_sync(Base.metadata.create_all)
        # Prozess-Versionierung (P6-S8): `name` war vorher global eindeutig,
        # ist jetzt der Prozessfamilien-Schlüssel - Eindeutigkeit gilt seither
        # für (name, version). `create_all` legt fehlende TABELLEN/Constraints
        # nur für neue Deployments an, ändert aber keine bestehenden - daher
        # ad-hoc für bereits existierende Datenbanken (kein Alembic in dieser
        # frühen Phase, siehe CONTRIBUTING.md). Postgres kennt kein
        # `ADD CONSTRAINT IF NOT EXISTS`, wohl aber `DROP CONSTRAINT IF EXISTS`
        # und `CREATE UNIQUE INDEX IF NOT EXISTS` - ein Unique-Index ist
        # äquivalent zur früheren Unique-Constraint-Durchsetzung.
        await conn.execute(
            text(
                "ALTER TABLE workflow.process_definition "
                "ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE workflow.process_definition "
                "DROP CONSTRAINT IF EXISTS process_definition_name_key"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_process_definition_name_version "
                "ON workflow.process_definition (name, version)"
            )
        )
        # Federation Hub (P6-S9): `federation_task.handover_id` war zunächst
        # allein eindeutig - im Selbst-Loopback-Smoke-Test (eine Installation
        # übergibt an sich selbst) trägt aber sowohl die outbound- als auch die
        # inbound-Zeile denselben `handover_id` in derselben Datenbank, siehe
        # `models.FederationTask`. Eindeutigkeit gilt seither für
        # `(handover_id, direction)` - gleiches idempotentes Migrationsmuster
        # wie bei der Prozessdefinition-Versionierung oben.
        await conn.execute(
            text("DROP INDEX IF EXISTS workflow.ix_workflow_federation_task_handover_id")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_workflow_federation_task_handover_id "
                "ON workflow.federation_task (handover_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_federation_task_handover_direction "
                "ON workflow.federation_task (handover_id, direction)"
            )
        )
        # Signaturbasierte Hub-Authentisierung statt API-Key (P13-S4,
        # ADR 0039) - `api_key` wird im selben Schritt entfernt statt bewusst
        # zurückgestellt: das alte Modell wird hier vollständig ersetzt, kein
        # Rolling-Update-Szenario zwischen alt/neu zu berücksichtigen, siehe
        # gleiche Begründung in `federation_hub_service.main`.
        await conn.execute(
            text("ALTER TABLE workflow.federation_identity DROP COLUMN IF EXISTS api_key")
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.signature_client = SignatureServiceClient(settings.signature_service_base_url)
    app.state.federation_client = await _ensure_federation_identity(app.state.session_factory)
    app.state.license_client = LicenseStatusClient(
        settings.registry_service_base_url or "",
        settings.service_name,
        settings.license_status_cache_ttl_seconds,
    )

    # Reiner Producer (kein Consumer, siehe docs/services/workflow-service.md
    # "Events") - eigener Stream, ein Producer muss ihn selbst anlegen (ADR 0001).
    event_bus = NatsEventBusClient(settings.nats_url, stream="workflow")
    await event_bus.connect()
    app.state.event_bus = event_bus

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    sla_poll_task = asyncio.create_task(
        _sla_poll_loop(app.state.session_factory, app.state.permission_client)
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sla_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await sla_poll_task
    if registration:
        await registration.stop()
    await event_bus.close()
    await app.state.permission_client.close()
    await app.state.signature_client.close()
    await app.state.license_client.close()
    if app.state.federation_client is not None:
        await app.state.federation_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(
    event_type: str, subject: str, payload: dict, actor: str | None = None
) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


async def _dispatch_outbound_federated_task(
    session: AsyncSession, instance_id: str, task: spiff_adapter.TaskInfo
) -> None:
    """Automatischer Handover (7.4): ein `taskType=federated`-Task wird nie
    von einem Menschen abgeschlossen (siehe `_reject_manual_federated_completion`),
    sondern sobald bereit sofort an den Federation Hub übergeben."""
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    target_installation_id = task.extensions.get("targetInstallationId")
    target_process_type = task.extensions.get("targetProcessType")
    if identity is None or not target_installation_id or not target_process_type:
        logger.warning(
            "federated_task_missing_configuration",
            extra={"instance_id": instance_id, "task_id": task.id},
        )
        return

    installations = await app.state.federation_client.list_installations()
    target = next((i for i in installations if i["id"] == target_installation_id), None)
    if target is None:
        logger.warning(
            "federated_task_unknown_target",
            extra={"instance_id": instance_id, "target_installation_id": target_installation_id},
        )
        return

    encrypted_payload = federation_crypto.encrypt_for(
        target["public_key_pem"].encode("utf-8"), task.data
    )

    # Der `handover_id` wird bewusst hier (nicht vom Hub) erzeugt und die
    # eigene FederationTask-Zeile bereits VOR dem Hub-Aufruf committet: der
    # Hub kann synchron bis zurück in diese Installation zustellen (z. B. ein
    # Handover an die eigene installation_id, siehe ADR 0028
    # "Selbst-Loopback") - ohne diesen Commit wäre die Zeile bei einem
    # verschachtelten Rückruf (`/federation/inbound-result`) noch nicht
    # sichtbar (Postgres-Transaktionsisolation), da die äußere Transaktion
    # erst nach Rückkehr aus `create_handover()` committet.
    handover_id = str(uuid.uuid4())
    await repository.create_federation_task(
        session,
        process_instance_id=instance_id,
        task_id=task.id,
        handover_id=handover_id,
        direction="outbound",
        origin_installation_id=None,
        status="pending",
    )
    await session.commit()

    try:
        handover = await app.state.federation_client.create_handover(
            installation_id=identity.installation_id,
            private_key_pem=identity.private_key_pem,
            handover_id=handover_id,
            to_installation_id=target_installation_id,
            process_type=target_process_type,
            encrypted_payload=encrypted_payload,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "federated_handover_creation_failed: %s",
            exc,
            extra={"instance_id": instance_id, "task_id": task.id},
        )
        return

    federation_task = await repository.get_federation_task_by_handover(session, handover_id)
    if federation_task is not None:
        await repository.update_federation_task_status(session, federation_task, handover["status"])
        await session.commit()


async def _dispatch_federated_return_task(
    session: AsyncSession, instance_id: str, task: spiff_adapter.TaskInfo
) -> None:
    """Gegenstück auf der Empfängerseite (7.4): schickt das Ergebnis eines
    `taskType=federated_return`-Task automatisch über den Federation Hub an
    die ursprüngliche Installation zurück, sobald der Task bereit ist."""
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    inbound = await repository.get_inbound_federation_task_for_instance(session, instance_id)
    if identity is None or inbound is None or inbound.origin_installation_id is None:
        logger.warning(
            "federated_return_task_missing_origin",
            extra={"instance_id": instance_id, "task_id": task.id},
        )
        return

    installations = await app.state.federation_client.list_installations()
    origin = next((i for i in installations if i["id"] == inbound.origin_installation_id), None)
    if origin is None:
        logger.warning(
            "federated_return_task_unknown_origin",
            extra={
                "instance_id": instance_id,
                "origin_installation_id": inbound.origin_installation_id,
            },
        )
        return

    encrypted_result = federation_crypto.encrypt_for(
        origin["public_key_pem"].encode("utf-8"), task.data
    )
    try:
        result = await app.state.federation_client.send_result(
            installation_id=identity.installation_id,
            private_key_pem=identity.private_key_pem,
            handover_id=inbound.handover_id,
            outcome="completed",
            encrypted_result=encrypted_result,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "federated_return_send_failed: %s",
            exc,
            extra={"instance_id": instance_id, "task_id": task.id},
        )
        return

    # Der Hub selbst antwortet mit 200, auch wenn die Weiterleitung an die
    # Ursprungsinstallation dahinter fehlschlägt (siehe
    # `federation_hub_service.main.submit_handover_result`) - `result["status"]`
    # spiegelt den tatsächlichen Zustellungserfolg, nicht nur die eigene
    # erfolgreiche Übergabe an den Hub.
    status = "returned" if result.get("status") == "completed" else "return_delivery_failed"
    await repository.mark_inbound_federation_task_returned(
        session, inbound, task_id=task.id, status=status
    )


async def _dispatch_pending_federation_tasks(session: AsyncSession, instance_id: str) -> None:
    """Wird nach jeder Operation aufgerufen, die neue bereite Tasks erzeugen
    kann (Instanzstart, Task-Abschluss, SLA-Poll-Tick, Federation-Inbound) -
    erkennt neu bereite `federated`/`federated_return`-Tasks und löst die
    passende Aktion aus. Bereits dispatchte Tasks werden über
    `FederationTask`-Zeilen übersprungen (keine doppelte Zustellung)."""
    if app.state.federation_client is None:
        return
    try:
        tasks = await repository.get_ready_tasks(session, instance_id)
    except repository.NotFoundError:
        return
    for task in tasks:
        task_type = task.extensions.get("taskType")
        if task_type not in _FEDERATED_TASK_TYPES:
            continue
        if await repository.get_federation_task_by_task(session, instance_id, task.id) is not None:
            continue
        if task_type == "federated":
            await _dispatch_outbound_federated_task(session, instance_id, task)
        else:
            await _dispatch_federated_return_task(session, instance_id, task)


async def _require_object_config(x_dms_principal: str) -> None:
    """Retrofit P6-S6: Prozessdefinitionen (inkl. Script-Task-Upload, laut
    `docs/services/workflow-service.md` "ein reales Sicherheitsthema") sind
    ab jetzt eine administrative Aktion, keine reguläre Fachnutzung -
    verlangt die Domain-Admin-Capability `admin.object_config` (dieselbe
    Rolle "Objekttyp-/Workflow-Konfiguration" aus P6-S5, jetzt zum ersten Mal
    tatsächlich durchgesetzt inkl. echtem technischen Konto `config-admin`).
    Instanzstart/Task-Abschluss bleiben bewusst für jeden authentifizierten
    Principal offen (normale Fachnutzung), siehe P6-S6-Rückfrage-Entscheidung."""
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, "admin.object_config"
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Objekttyp-/Workflow-Konfiguration'",
        )


async def _reject_during_maintenance(x_dms_maintenance_active: str) -> None:
    """Not-Shutdown (4.8, P6-S6): "alle laufenden Workflow-Instanzen ...
    angehalten" wird als "keine neuen Instanzen/keine Fortschritte während
    der Sperre" umgesetzt (siehe ADR 0024 für die Begründung der Grenze)."""
    if x_dms_maintenance_active.lower() == "true":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Wartungsmodus",
        )


def _license_gate(action: Literal["read", "write"]):
    """Demo-Modus/Sperrverhalten (Konzept 9.3, P9-S2): workflow-service ist
    die einzige heute real existierende licensierbare "Applikationskomponente"
    (9.1). `"unlicensed"` sperrt vollstaendig (auch Lesen), `"demo"` erlaubt
    nur Lesezugriff (Konzept-Beispiel woertlich). Als `Depends()` statt wie
    beim Wartungsmodus manuell im Funktionskoerper, da hier ein async
    Cross-Service-Aufruf noetig ist, kein simpler Header-Read. Federation-
    Endpunkte bleiben bewusst aussen vor (eigenstaendiges Thema, Phase 13)."""

    async def _check() -> None:
        license_status = await app.state.license_client.get_status()
        if license_status == "unlicensed":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Lizenz erforderlich - Komponente 'workflow-service' nicht lizenziert.",
            )
        if license_status == "demo" and action == "write":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Demo-Modus aktiv - nur Lesezugriff verfügbar.",
            )

    return _check


@app.post(
    "/process-definitions",
    response_model=ProcessDefinitionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_license_gate("write"))],
)
async def create_process_definition(
    bpmn_xml: UploadFile = File(...),
    name: str = Form(...),
    process_id: str | None = Form(None),
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ProcessDefinitionOut:
    await _require_object_config(x_dms_principal)
    xml_bytes = await bpmn_xml.read()
    try:
        xml_text = xml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="bpmn_xml ist kein gültiges UTF-8") from exc

    try:
        definition = await repository.create_process_definition(
            session, name=name, bpmn_xml=xml_text, process_id=process_id
        )
    except repository.InvalidBpmnError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return definition


@app.get(
    "/process-definitions",
    response_model=list[ProcessDefinitionOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def list_process_definitions(
    name: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[ProcessDefinitionOut]:
    """Ohne `name`: neueste Version je Prozessfamilie (P6-S8). Mit `name`:
    vollständige Versionshistorie dieser Familie, neueste zuerst."""
    return await repository.list_process_definitions(session, name=name)


@app.get(
    "/process-definitions/{process_definition_id}",
    response_model=ProcessDefinitionDetailOut,
    dependencies=[Depends(_license_gate("read"))],
)
async def get_process_definition(
    process_definition_id: int, session: AsyncSession = Depends(get_session)
) -> ProcessDefinitionDetailOut:
    try:
        return await repository.get_process_definition(session, process_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/process-definitions/{process_definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_license_gate("write"))],
)
async def delete_process_definition(
    process_definition_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_object_config(x_dms_principal)
    try:
        await repository.delete_process_definition(session, process_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.ProcessDefinitionInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()


@app.post(
    "/process-definitions/{process_definition_id}/instances",
    response_model=ProcessInstanceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_license_gate("write"))],
)
async def start_instance(
    process_definition_id: int,
    payload: ProcessInstanceCreate,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    try:
        instance = await repository.start_instance(
            session,
            process_definition_id,
            created_by=payload.created_by,
            business_key=payload.business_key,
            initial_data=payload.initial_data,
            instance_id=payload.instance_id,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.InvalidBpmnError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        # Ein automatischer Schritt (z. B. `connector_call`, P12-S2) kann fehlschlagen,
        # NACHDEM `repository.start_instance()` die Instanz bereits mit dem aktuellen
        # (ggf. `ERROR`-)Zwischenstand geflusht hat (siehe deren `try`/`finally`) - ohne
        # dieses Commit hier würde `get_session()`s Context-Manager den Flush beim
        # Schließen der Session zurückrollen (`AsyncSession.close()` ohne vorheriges
        # `commit()`), und `POST /instances/{id}/retry` fände gar keine Instanz vor.
        await session.commit()
        raise
    await session.commit()
    await publish_event(
        "workflow.instance.started",
        subject=instance.id,
        payload={"process_definition_id": process_definition_id, "created_by": payload.created_by},
        actor=payload.created_by,
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance.id,
            payload={"business_key": instance.business_key},
            actor=payload.created_by,
        )
    await _dispatch_pending_federation_tasks(session, instance.id)
    await session.commit()
    return instance


@app.get(
    "/instances/{instance_id}",
    response_model=ProcessInstanceOut,
    dependencies=[Depends(_license_gate("read"))],
)
async def get_instance(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> ProcessInstanceOut:
    try:
        return await repository.get_instance(session, instance_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/instances",
    response_model=list[ProcessInstanceOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def list_instances(
    process_definition_id: int | None = None,
    status: str | None = None,
    business_key: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ProcessInstanceOut]:
    return await repository.list_instances(
        session,
        process_definition_id=process_definition_id,
        status=status,
        business_key=business_key,
    )


@app.get(
    "/tasks",
    response_model=list[ReadyTaskWithInstanceOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def list_ready_tasks(
    session: AsyncSession = Depends(get_session),
) -> list[ReadyTaskWithInstanceOut]:
    """Cross-Instanz-Aufgabenliste (8, P14-S2 Reviewer/Approval-UI) - bislang
    gab es nur `GET /instances/{id}/tasks` (verlangt eine bereits bekannte
    Instanz-ID). Iteriert alle `running`-Instanzen (gleiches Muster wie der
    SLA-Poll-Loop, `_sla_poll_loop`) und sammelt deren bereite Manual-/
    Signature-Tasks ein. `federated`/`federated_return`-Tasks werden
    herausgefiltert - die werden ausschließlich automatisch über den
    Federation Hub abgeschlossen (`_reject_manual_federated_completion`),
    ein Mensch könnte sie über diese Liste ohnehin nie erfolgreich
    abschließen. Kein zusätzliches Rollen-Gate über die Lizenzprüfung
    hinaus, wie bei Instanzstart/Task-Abschluss selbst."""
    instances = await repository.list_instances(session, status="running")
    tasks: list[ReadyTaskWithInstanceOut] = []
    for instance in instances:
        for task in await repository.get_ready_tasks(session, instance.id):
            if task.extensions.get("taskType") in _FEDERATED_TASK_TYPES:
                continue
            tasks.append(
                ReadyTaskWithInstanceOut(
                    id=task.id,
                    name=task.name,
                    lane=task.lane,
                    data=task.data,
                    extensions=task.extensions,
                    instance_id=instance.id,
                    process_definition_id=instance.process_definition_id,
                    business_key=instance.business_key,
                )
            )
    return tasks


@app.get(
    "/instances/{instance_id}/tasks",
    response_model=list[ReadyTaskOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def get_ready_tasks(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> list[ReadyTaskOut]:
    try:
        tasks = await repository.get_ready_tasks(session, instance_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        ReadyTaskOut(id=t.id, name=t.name, lane=t.lane, data=t.data, extensions=t.extensions)
        for t in tasks
    ]


async def _require_valid_signature_if_needed(
    session: AsyncSession, instance_id: str, task_id: str, payload: TaskCompleteRequest
) -> None:
    """Signature Task (3.10, P6-S7): eine als `taskType=signature` markierte
    Task (siehe `spiff_adapter.py`s Camunda-Extensions) verlangt eine echte,
    beim Signature Service erzeugte Signatur statt eines beliebigen
    Abschlusses - `document_id` kommt aus der generischen Task-Prozessdaten
    (`data`, kein eigenes Schema-Feld, da workflow-service kein
    Dokument-Konzept kennt), `requiredLevel` aus den BPMN-Extensions
    (Default `ses`, falls nicht gesetzt)."""
    tasks = await repository.get_ready_tasks(session, instance_id)
    task = next((t for t in tasks if t.id == task_id), None)
    if task is None or task.extensions.get("taskType") != "signature":
        return

    if not payload.signature_id:
        raise HTTPException(
            status_code=400, detail="Signature Task verlangt eine signature_id im Request-Body"
        )
    signature = await app.state.signature_client.get_signature(payload.signature_id)
    if signature is None:
        raise HTTPException(
            status_code=400, detail=f"signature_id {payload.signature_id!r} unbekannt"
        )

    expected_document_id = task.data.get("document_id")
    if not expected_document_id:
        raise HTTPException(
            status_code=400,
            detail="Signature Task ohne document_id in den Prozessdaten - nicht signierbar",
        )
    if signature["document_id"] != expected_document_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"signature_id {payload.signature_id!r} gehört zu Dokument "
                f"{signature['document_id']!r}, erwartet wurde {expected_document_id!r}"
            ),
        )

    required_level = task.extensions.get("requiredLevel", "ses")
    if _SIGNATURE_LEVEL_RANK[signature["level"]] < _SIGNATURE_LEVEL_RANK[required_level]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Signature Task verlangt mindestens Niveau {required_level!r}, "
                f"signature_id {payload.signature_id!r} hat Niveau {signature['level']!r}"
            ),
        )


async def _reject_manual_federated_completion(
    session: AsyncSession, instance_id: str, task_id: str
) -> None:
    """Ein `federated`/`federated_return`-Task (7.4, P6-S9) wird ausschließlich
    automatisch über den Federation Hub abgeschlossen (siehe
    `_dispatch_pending_federation_tasks`) - ein direkter `.../complete`-Aufruf
    (versehentlich oder durch einen Menschen) wird abgelehnt, sonst könnte ein
    Handover-Ergebnis nie mehr zugestellt werden, weil der Task lokal bereits
    fertig ist."""
    tasks = await repository.get_ready_tasks(session, instance_id)
    task = next((t for t in tasks if t.id == task_id), None)
    if task is not None and task.extensions.get("taskType") in _FEDERATED_TASK_TYPES:
        raise HTTPException(
            status_code=409,
            detail="Dieser Task wird automatisch über den Federation Hub abgeschlossen",
        )


@app.post(
    "/instances/{instance_id}/tasks/{task_id}/complete",
    response_model=ProcessInstanceOut,
    dependencies=[Depends(_license_gate("write"))],
)
async def complete_task(
    instance_id: str,
    task_id: str,
    payload: TaskCompleteRequest,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    try:
        await _require_valid_signature_if_needed(session, instance_id, task_id, payload)
        await _reject_manual_federated_completion(session, instance_id, task_id)
        instance = await repository.complete_task(
            session, instance_id, task_id, completed_by=payload.completed_by, data=payload.data
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.TaskNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        # Siehe `start_instance` - ein nachfolgender automatischer Schritt kann
        # fehlschlagen, der bereits geflushte Zwischenstand muss trotzdem committet
        # werden (P12-S2 Resumability).
        await session.commit()
        raise
    await session.commit()
    await publish_event(
        "workflow.task.completed",
        subject=instance_id,
        payload={"task_id": task_id, "completed_by": payload.completed_by},
        actor=payload.completed_by,
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance_id,
            payload={"business_key": instance.business_key},
            actor=payload.completed_by,
        )
    await _dispatch_pending_federation_tasks(session, instance_id)
    await session.commit()
    return instance


@app.post(
    "/instances/{instance_id}/retry",
    response_model=ProcessInstanceOut,
    dependencies=[Depends(_license_gate("write"))],
)
async def retry_instance(
    instance_id: str,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    """Resumability für einen fehlgeschlagenen automatischen Schritt (7.1/7.2, P12-S2) -
    generisches Primitiv, kein migrationsspezifischer Endpunkt: ein `connector_call`-
    Service-Task, dessen `serviceUrl` beim ersten Versuch nicht erreichbar war, lässt
    die Instanz `running` mit dem betroffenen Task in `ERROR` zurück (siehe
    `spiff_adapter.retry_errored_tasks`) - dieser Endpunkt versucht den Schritt erneut,
    ohne den gesamten Prozess neu zu starten. Kein zusätzliches Rollen-Gate über die
    normale Lizenzprüfung hinaus - bereits `POST .../tasks/.../complete` ist für jeden
    authentifizierten Principal offen."""
    await _reject_during_maintenance(x_dms_maintenance_active)
    try:
        instance = await repository.retry_instance(session, instance_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.InstanceNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        # Siehe `start_instance` - ein erneut fehlschlagender Versuch muss trotzdem
        # committet werden, sonst bliebe die Instanz für einen dritten `retry`-Versuch
        # am ursprünglichen statt am tatsächlich letzten Fehlerpunkt hängen.
        await session.commit()
        raise
    await session.commit()
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance_id,
            payload={"business_key": instance.business_key},
            actor="system:retry",
        )
        await session.commit()
    return instance


@app.get("/federation/installations")
async def list_federation_installations() -> list[dict]:
    """Proxy auf das Hub-Adressbuch (7.4) - ungegated wie andere `GET`s, siehe
    `docs/services/process-designer.md`. Ohne konfigurierten Hub eine leere
    Liste, damit der Process Designer föderierte Prozessschritte gar nicht
    erst als Option anbietet (Konzept 7.1)."""
    if app.state.federation_client is None:
        return []
    return await app.state.federation_client.list_installations()


@app.get("/federation/config", response_model=FederationConfigOut)
async def get_federation_config(session: AsyncSession = Depends(get_session)) -> FederationConfig:
    """7.4/P13-S3: aktuell erklärte Versionskompatibilitätsspanne - ungegated
    wie die übrigen Federation-Lese-Endpunkte, reine Metadaten (keine
    Dokumentinhalte, kein Geheimnis)."""
    config = await _get_or_seed_federation_config(session)
    await session.commit()
    return config


@app.put("/federation/config", response_model=FederationConfigOut)
async def update_federation_config(
    payload: FederationConfigUpdate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> FederationConfig:
    """7.4/P13-S3: macht die Versionskompatibilitätsspanne über den regulären
    Konfigurationsimport (7.3, `config-service`) änderbar, ohne einen
    Container-Neustart zu benötigen - gegated hinter derselben
    `admin.object_config`-Capability wie der BPMN-Upload (P6-S6-Retrofit),
    da `config-service` sich diese Rolle bereits selbst zuweist. Stößt bei
    aktiver Föderation sofort eine Re-Registrierung beim Hub mit den neuen
    Werten an (Fehlschlag blockiert die Antwort nicht - Federation bleibt ein
    Zusatznutzen, kein Hard-Dependency)."""
    await _require_object_config(x_dms_principal)
    config = await _get_or_seed_federation_config(session)
    config.version = payload.version
    config.min_compatible_peer_version = payload.min_compatible_peer_version
    config.updated_at = datetime.now(UTC)
    await session.commit()

    if app.state.federation_client is not None:
        identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
        if identity is not None:
            callback_base_url = f"{settings.installation_gateway_base_url}/api/workflow-service"
            try:
                await app.state.federation_client.register(
                    installation_id=identity.installation_id,
                    private_key_pem=identity.private_key_pem,
                    display_name=settings.installation_display_name,
                    callback_base_url=callback_base_url,
                    public_key_pem=identity.public_key_pem.decode("utf-8"),
                    version=config.version,
                    min_compatible_peer_version=config.min_compatible_peer_version,
                )
            except httpx.HTTPError:
                logger.warning("federation_hub_reregistration_after_config_update_failed")
    return config


@app.post("/federation/rotate-key")
async def rotate_federation_key(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Schlüsselrotation (7.4, P13-S4, ADR 0039) - gegated hinter derselben
    `admin.object_config`-Capability wie `PUT /federation/config`. Generiert
    lokal ein frisches RSA-Schlüsselpaar, signiert die Rotationsanfrage mit
    dem noch aktuellen (alten) privaten Schlüssel (Kontinuitätsnachweis
    gegenüber dem Hub, siehe `federation_hub_service.repository.
    rotate_installation_key`) und übernimmt das neue Schlüsselpaar erst nach
    einer erfolgreichen Antwort des Hub - schlägt die Rotation fehl, bleibt
    das alte, weiterhin gültige Schlüsselpaar unverändert im Einsatz."""
    await _require_object_config(x_dms_principal)
    if app.state.federation_client is None:
        raise HTTPException(status_code=503, detail="Federation Hub nicht konfiguriert")
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    if identity is None:
        raise HTTPException(status_code=503, detail="Noch keine Federation-Identität vorhanden")

    new_private_pem, new_public_pem = federation_crypto.generate_keypair()
    try:
        await app.state.federation_client.rotate_key(
            installation_id=identity.installation_id,
            old_private_key_pem=identity.private_key_pem,
            new_public_key_pem=new_public_pem.decode("utf-8"),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Schlüsselrotation beim Hub fehlgeschlagen: {exc}",
        ) from exc

    identity.private_key_pem = new_private_pem
    identity.public_key_pem = new_public_pem
    await session.commit()
    return {"installation_id": identity.installation_id, "rotated_at": datetime.now(UTC)}


async def _verify_hub_signature(
    session: AsyncSession, body: bytes, signature: str
) -> FederationIdentity:
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    if identity is None:
        raise HTTPException(status_code=503, detail="Federation Hub nicht konfiguriert")
    if not federation_crypto.verify_body(identity.hub_public_key_pem, body, signature):
        raise HTTPException(status_code=401, detail="Ungültige oder fehlende Hub-Signatur")
    return identity


@app.post(
    "/federation/inbound", response_model=ProcessInstanceOut, status_code=status.HTTP_201_CREATED
)
async def federation_inbound(
    request: Request,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    """Empfängt eine neue, vom Federation Hub vermittelte Übergabe (7.4) -
    startet lokal eine neue Instanz des über
    `settings.federation_process_type_map` zugeordneten Prozesses. Bewusst
    öffentlich (kein `X-DMS-Principal`, siehe `gateway-service`s
    `public_routes`) - authentisiert wird stattdessen über die
    `X-Federation-Hub-Signature`. Respektiert wie Instanzstart/Task-Abschluss
    den Wartungsmodus (4.8) - ein föderierter Schritt ist Alltagsverarbeitung,
    kein Admin-Vorgang."""
    await _reject_during_maintenance(x_dms_maintenance_active)
    body = await request.body()
    identity = await _verify_hub_signature(
        session, body, request.headers.get("X-Federation-Hub-Signature", "")
    )
    payload = json.loads(body)
    process_type = payload["process_type"]
    local_name = settings.federation_process_type_map.get(process_type)
    if local_name is None:
        raise HTTPException(
            status_code=422, detail=f"Kein lokales Prozess-Mapping für {process_type!r}"
        )
    definitions = await repository.list_process_definitions(session, name=local_name)
    if not definitions:
        raise HTTPException(status_code=422, detail=f"Keine Prozessdefinition {local_name!r}")
    try:
        decrypted_payload = federation_crypto.decrypt_with(
            identity.private_key_pem, payload["encrypted_payload"]
        )
    except federation_crypto.DecryptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    instance = await repository.start_instance(
        session,
        definitions[0].id,  # neueste Version zuerst, siehe list_process_definitions
        created_by="federation-hub",
        business_key=None,
        initial_data=decrypted_payload,
    )
    await repository.create_federation_task(
        session,
        process_instance_id=instance.id,
        task_id=None,
        handover_id=payload["handover_id"],
        direction="inbound",
        origin_installation_id=payload["from_installation_id"],
        status="received",
    )
    await session.commit()
    await publish_event(
        "workflow.instance.started",
        subject=instance.id,
        payload={"process_definition_id": definitions[0].id, "created_by": "federation-hub"},
        actor="system:federation-hub",
    )
    await publish_event(
        "workflow.federation.inbound_received",
        subject=instance.id,
        payload={
            "business_key": instance.business_key,
            "from_installation_id": payload["from_installation_id"],
            "process_type": process_type,
            "notify_email": decrypted_payload.get("notify_email"),
        },
        actor="system:federation-hub",
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance.id,
            payload={"business_key": instance.business_key},
            actor="system:federation-hub",
        )
    await _dispatch_pending_federation_tasks(session, instance.id)
    await session.commit()
    return instance


@app.post("/federation/inbound-result")
async def federation_inbound_result(
    request: Request,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Empfängt die Rückmeldung eines zuvor selbst initiierten Handover (7.4) -
    schließt den ursprünglich wartenden `taskType=federated`-Task
    programmatisch ab (nicht über den regulären `.../complete`-Endpunkt, der
    genau diesen Task-Typ ablehnt, siehe `_reject_manual_federated_completion`).
    Respektiert wie andere Fachverarbeitung den Wartungsmodus (4.8)."""
    await _reject_during_maintenance(x_dms_maintenance_active)
    body = await request.body()
    identity = await _verify_hub_signature(
        session, body, request.headers.get("X-Federation-Hub-Signature", "")
    )
    payload = json.loads(body)
    federation_task = await repository.get_federation_task_by_handover(
        session, payload["handover_id"]
    )
    if federation_task is None or federation_task.task_id is None:
        raise HTTPException(
            status_code=404, detail=f"handover_id {payload['handover_id']!r} unbekannt"
        )
    try:
        result_data = federation_crypto.decrypt_with(
            identity.private_key_pem, payload["encrypted_result"]
        )
    except federation_crypto.DecryptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        instance = await repository.complete_task(
            session,
            federation_task.process_instance_id,
            federation_task.task_id,
            completed_by="federation-hub",
            data=result_data,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.TaskNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await repository.update_federation_task_status(session, federation_task, "completed")
    await session.commit()
    await publish_event(
        "workflow.task.completed",
        subject=federation_task.process_instance_id,
        payload={"task_id": federation_task.task_id, "completed_by": "federation-hub"},
        actor="system:federation-hub",
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance.id,
            payload={"business_key": instance.business_key},
            actor="system:federation-hub",
        )
    await _dispatch_pending_federation_tasks(session, instance.id)
    await session.commit()
    return {"status": "ok"}
