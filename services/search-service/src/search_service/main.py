import logging
import time

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, Request
from search_service import repository
from search_service.consumer import start_consuming_documents, start_consuming_text_updates
from search_service.document_client import DocumentServiceClient
from search_service.folder_client import FolderServiceClient
from search_service.models import Base
from search_service.object_type_client import ObjectTypeServiceClient
from search_service.ocr_client import OcrServiceClient
from search_service.permission_client import PermissionServiceClient
from search_service.rendering_client import RenderingServiceClient
from search_service.repository import AttrFilter
from search_service.settings import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_SNIPPET_LENGTH = 200


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS search"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.document_client = DocumentServiceClient(settings.document_service_base_url)
    app.state.folder_client = FolderServiceClient(settings.folder_service_base_url)
    app.state.object_type_client = ObjectTypeServiceClient(settings.object_type_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.ocr_client = OcrServiceClient(settings.ocr_service_base_url)
    app.state.rendering_client = RenderingServiceClient(settings.rendering_service_base_url)

    # Reiner Konsument (kein eigener Stream, kein Publisher) - Search Service
    # veröffentlicht keine Events, dieselbe Rolle wie audit-service.
    event_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await event_bus.connect()
    app.state.event_bus = event_bus

    client_kwargs = {
        "session_factory": app.state.session_factory,
        "document_client": app.state.document_client,
        "folder_client": app.state.folder_client,
        "ocr_client": app.state.ocr_client,
        "rendering_client": app.state.rendering_client,
    }
    await start_consuming_documents(event_bus, settings.document_subjects, **client_kwargs)
    await start_consuming_text_updates(
        event_bus, settings.ocr_subjects + settings.rendering_subjects, **client_kwargs
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    if registration:
        await registration.stop()
    await event_bus.close()
    await app.state.document_client.close()
    await app.state.folder_client.close()
    await app.state.object_type_client.close()
    await app.state.permission_client.close()
    await app.state.ocr_client.close()
    await app.state.rendering_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


def _snippet(full_text: str) -> str:
    text_value = " ".join(full_text.split())
    if len(text_value) <= _SNIPPET_LENGTH:
        return text_value
    return text_value[:_SNIPPET_LENGTH].rsplit(" ", 1)[0] + "…"


async def _parse_attr_filters(
    request: Request, object_type_id: int | None, object_type_client: ObjectTypeServiceClient
) -> list[AttrFilter]:
    attr_params = [(k, v) for k, v in request.query_params.items() if k.startswith("attr.")]
    if not attr_params:
        return []
    if object_type_id is None:
        raise HTTPException(status_code=400, detail="Attributfilter erfordern object_type_id")
    object_type = await object_type_client.get(object_type_id)
    if object_type is None:
        raise HTTPException(status_code=400, detail=f"object_type_id {object_type_id!r} unbekannt")
    attr_types = {attr["name"]: attr.get("type", "string") for attr in object_type["attributes"]}

    filters: list[AttrFilter] = []
    for key, value in attr_params:
        rest = key.removeprefix("attr.")
        if rest.endswith(".gte"):
            name, op = rest.removesuffix(".gte"), "gte"
        elif rest.endswith(".lte"):
            name, op = rest.removesuffix(".lte"), "lte"
        else:
            name, op = rest, "eq"
        attr_type = attr_types.get(name, "string")
        filters.append(AttrFilter(name=name, op=op, value=value, attr_type=attr_type))
    return filters


@app.get("/search")
async def search(
    request: Request,
    q: str | None = None,
    folder_id: str | None = None,
    object_type_id: int | None = None,
    created_by: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
    sort: Literal["relevance", "created_at", "updated_at"] = "relevance",
    session: AsyncSession = Depends(get_session),
) -> dict:
    principal_id = request.headers.get("x-dms-principal")
    if not principal_id:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")

    limit = min(limit, 100)
    attr_filters = await _parse_attr_filters(request, object_type_id, app.state.object_type_client)

    internal_limit = min(
        (limit + offset) * settings.search_result_overfetch_factor,
        settings.search_result_hard_limit,
    )
    rows = await repository.search(
        session,
        query=q,
        folder_id=folder_id,
        object_type_id=object_type_id,
        created_by=created_by,
        created_after=created_after,
        created_before=created_before,
        attr_filters=attr_filters,
        limit=internal_limit,
        offset=0,
        sort=sort,
    )

    resource_ids = {doc.folder_id or "root" for doc, _rank in rows}
    allowed = await app.state.permission_client.check_batch(
        principal_id=principal_id,
        permission="document.read",
        access_type="read",
        resource_ids=list(resource_ids),
    )
    readable = [
        (doc, rank) for doc, rank in rows if allowed.get(doc.folder_id or "root", False)
    ]
    page = readable[offset : offset + limit]

    facets = await repository.facet_counts(
        session,
        query=q,
        folder_id=folder_id,
        object_type_id=object_type_id,
        created_by=created_by,
        created_after=created_after,
        created_before=created_before,
        attr_filters=attr_filters,
    )

    return {
        "results": [
            {
                "id": doc.document_id,
                "title": doc.title,
                "folder_id": doc.folder_id,
                "folder_name": doc.folder_name,
                "object_type_id": doc.object_type_id,
                "attributes": doc.attributes,
                "current_version_number": doc.current_version_number,
                "deleted_at": None,
                "created_by": doc.created_by,
                "created_at": doc.created_at,
                "updated_at": doc.updated_at,
                "rank": float(rank) if rank is not None else 0.0,
                "snippet": _snippet(doc.full_text),
            }
            for doc, rank in page
        ],
        "total_returned": len(page),
        "facet_counts": facets,
    }


@app.get("/search/facets")
async def search_facets() -> dict:
    object_types = await app.state.object_type_client.list()
    return {
        "object_types": [
            {"id": ot["id"], "name": ot["name"], "attributes": ot["attributes"]}
            for ot in object_types
            if ot["applies_to"] == "document"
        ]
    }
