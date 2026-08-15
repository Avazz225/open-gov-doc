import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_constraint_engine import ROOT_PARENT_TYPE
from dms_constraint_engine import validate as run_validation
from dms_db_base import build_engine, make_session_factory
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from object_type_service import repository
from object_type_service.layout import generate_smart_layout
from object_type_service.models import Base, ObjectType
from object_type_service.schemas import (
    KennzeichenConfigIn,
    KennzeichenConfigOut,
    KennzeichenOut,
    LayoutIn,
    LayoutOut,
    LayoutPurpose,
    NextKennzeichenRequest,
    ObjectTypeCreate,
    ObjectTypeOut,
    ObjectTypeUpdate,
    ValidateRequest,
    ValidateResult,
)
from object_type_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS object_type"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc schema extension (no Alembic at this early phase, see
        # CONTRIBUTING.md): `create_all` creates missing TABLES, but doesn't
        # alter existing ones - both columns were only added in P5b-S1 (2.2a).
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS allowed_parent_types JSON"
            )
        )
        await conn.execute(
            text("ALTER TABLE object_type.object_type ADD COLUMN IF NOT EXISTS icon VARCHAR(64)")
        )
        # Reference number generator columns (P5e-S1) - `object_type_sequence`
        # is a new table and is already created by `create_all`.
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS kennzeichen_format VARCHAR(256)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS kennzeichen_display_override BOOLEAN"
            )
        )
        # Minimum signature level (3.10, P6-S7) - same ad-hoc migration pattern.
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS required_signature_level VARCHAR(8)"
            )
        )
        # Retention (5.2, P7-S1) - same ad-hoc migration pattern.
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS default_retention_days INTEGER"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS deletion_reason_required_override BOOLEAN"
            )
        )
        # Records disposal (5.6, P7-S3) - same ad-hoc migration pattern.
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS default_archive_after_days INTEGER"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS archive_encryption_enabled BOOLEAN NOT NULL DEFAULT false"
            )
        )
        # Classified-documents classification (2.5, P15-S1, multi-level since
        # P17-S2) - same ad-hoc migration pattern. `is_classified` (the
        # purely binary predecessor field up to P17-S1) is deliberately no
        # longer created here - an already-running installation keeps the
        # old column unused in the DB (no data loss, no destructive
        # migration), and a fresh stack never creates it in the first place.
        await conn.execute(
            text(
                "ALTER TABLE object_type.object_type "
                "ADD COLUMN IF NOT EXISTS classification_level VARCHAR(32)"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=http_sensor_declarations(),
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.post("/object-types", response_model=ObjectTypeOut, status_code=status.HTTP_201_CREATED)
async def create_object_type(
    payload: ObjectTypeCreate, session: AsyncSession = Depends(get_session)
) -> ObjectTypeOut:
    try:
        object_type = await repository.create_object_type(session, payload)
    except repository.DuplicateNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repository.InvalidFieldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return object_type


@app.get("/object-types", response_model=list[ObjectTypeOut])
async def list_object_types(
    applies_to: str | None = None,
    is_classified: bool | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ObjectTypeOut]:
    return await repository.list_object_types(
        session, applies_to=applies_to, is_classified=is_classified
    )


@app.get("/object-types/{object_type_id}", response_model=ObjectTypeOut)
async def get_object_type(
    object_type_id: int, session: AsyncSession = Depends(get_session)
) -> ObjectTypeOut:
    try:
        return await repository.get_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/object-types/{object_type_id}", response_model=ObjectTypeOut)
async def update_object_type(
    object_type_id: int, payload: ObjectTypeUpdate, session: AsyncSession = Depends(get_session)
) -> ObjectTypeOut:
    try:
        object_type = await repository.update_object_type(session, object_type_id, payload)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.InvalidFieldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return object_type


@app.delete("/object-types/{object_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object_type(
    object_type_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.post("/object-types/{object_type_id}/validate", response_model=ValidateResult)
async def validate_against_object_type(
    object_type_id: int, payload: ValidateRequest, session: AsyncSession = Depends(get_session)
) -> ValidateResult:
    try:
        object_type: ObjectType = await repository.get_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Resolution of placement information (2.2a): the caller only knows the
    # object_type_id of the parent folder (or that it is the root) - the name
    # is resolved here so that Object-Type Service remains the single source
    # for object type names (no extra roundtrip needed by the caller).
    if payload.parent_is_root:
        parent_type_name = ROOT_PARENT_TYPE
    elif payload.parent_object_type_id is not None:
        parent_object_type = await session.get(ObjectType, payload.parent_object_type_id)
        parent_type_name = parent_object_type.name if parent_object_type is not None else None
    else:
        parent_type_name = None

    schema = {
        "attributes": object_type.attributes,
        "namingConstraints": object_type.naming_constraints,
        "conditions": object_type.conditions,
        "allowedParentTypes": object_type.allowed_parent_types,
    }
    errors = run_validation(
        schema, name=payload.name, attributes=payload.attributes, parent_type_name=parent_type_name
    )
    return ValidateResult(valid=not errors, errors=errors)


@app.post("/object-types/{object_type_id}/next-kennzeichen", response_model=KennzeichenOut)
async def next_kennzeichen(
    object_type_id: int,
    payload: NextKennzeichenRequest = NextKennzeichenRequest(),
    session: AsyncSession = Depends(get_session),
) -> KennzeichenOut:
    try:
        kennzeichen = await repository.generate_next_kennzeichen(
            session, object_type_id, payload.attributes
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.NoKennzeichenFormatError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.MissingKennzeichenAttributeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return KennzeichenOut(kennzeichen=kennzeichen)


@app.get("/kennzeichen-config", response_model=KennzeichenConfigOut)
async def get_kennzeichen_config(
    session: AsyncSession = Depends(get_session),
) -> KennzeichenConfigOut:
    config = await repository.get_kennzeichen_config(session)
    await session.commit()
    return config


@app.put("/kennzeichen-config", response_model=KennzeichenConfigOut)
async def put_kennzeichen_config(
    body: KennzeichenConfigIn, session: AsyncSession = Depends(get_session)
) -> KennzeichenConfigOut:
    config = await repository.update_kennzeichen_config(
        session, show_before_filename=body.show_before_filename
    )
    await session.commit()
    return config


@app.get("/object-types/{object_type_id}/layouts/{purpose}", response_model=LayoutOut)
async def get_layout(
    object_type_id: int, purpose: LayoutPurpose, session: AsyncSession = Depends(get_session)
) -> LayoutOut:
    try:
        object_type = await repository.get_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    stored = await repository.get_layout(session, object_type_id, purpose.value)
    if stored is not None:
        return LayoutOut(**stored.layout, is_custom=True)
    generated = generate_smart_layout(object_type.attributes)
    return LayoutOut(**generated, is_custom=False)


@app.put("/object-types/{object_type_id}/layouts/{purpose}", response_model=LayoutOut)
async def put_layout(
    object_type_id: int,
    purpose: LayoutPurpose,
    payload: LayoutIn,
    session: AsyncSession = Depends(get_session),
) -> LayoutOut:
    try:
        layout_row = await repository.upsert_layout(session, object_type_id, purpose.value, payload)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.InvalidFieldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return LayoutOut(**layout_row.layout, is_custom=True)


@app.delete(
    "/object-types/{object_type_id}/layouts/{purpose}", status_code=status.HTTP_204_NO_CONTENT
)
async def reset_layout(
    object_type_id: int, purpose: LayoutPurpose, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.get_object_type(session, object_type_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await repository.delete_layout(session, object_type_id, purpose.value)
    await session.commit()
