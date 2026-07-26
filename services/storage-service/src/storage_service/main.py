import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import repository
from storage_service.backends import ObjectNotFoundError, S3Backend, build_backend
from storage_service.models import Base
from storage_service.schemas import ObjectMetadataOut, VerifyResult
from storage_service.settings import Settings

settings = Settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS storage"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    backend = build_backend(settings)
    if isinstance(backend, S3Backend):
        await backend.ensure_bucket()
    app.state.backend = backend

    yield

    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name, "backend": settings.backend}


@app.put("/objects/{key:path}", response_model=ObjectMetadataOut, status_code=201)
async def upload_object(
    key: str, request: Request, session: AsyncSession = Depends(get_session)
) -> ObjectMetadataOut:
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Leerer Request-Body")

    await app.state.backend.write(key, data)

    metadata = await repository.upsert_metadata(
        session,
        object_key=key,
        backend=settings.backend,
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        content_type=request.headers.get("content-type"),
    )
    await session.commit()
    return metadata


@app.get("/objects/{key:path}")
async def download_object(key: str, session: AsyncSession = Depends(get_session)) -> Response:
    try:
        metadata = await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    try:
        data = await app.state.backend.read(key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Objekt im Backend nicht (mehr) vorhanden"
        ) from exc

    return Response(content=data, media_type=metadata.content_type or "application/octet-stream")


@app.delete("/objects/{key:path}", status_code=204)
async def delete_object(key: str, session: AsyncSession = Depends(get_session)) -> None:
    try:
        await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    try:
        await app.state.backend.delete(key)
    except ObjectNotFoundError:
        pass  # bereits weg - Löschung bleibt idempotent

    await repository.delete_metadata(session, key)
    await session.commit()


@app.get("/object-metadata/{key:path}", response_model=ObjectMetadataOut)
async def get_object_metadata(
    key: str, session: AsyncSession = Depends(get_session)
) -> ObjectMetadataOut:
    try:
        return await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc


@app.get("/object-verify/{key:path}", response_model=VerifyResult)
async def verify_object(key: str, session: AsyncSession = Depends(get_session)) -> VerifyResult:
    """Fixity-Check-Grundlage (3.6): Prüfsumme neu aus dem Backend lesen und
    gegen den in der Shared DB hinterlegten Referenzwert abgleichen."""
    try:
        metadata = await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    actual = await app.state.backend.checksum(key)
    return VerifyResult(
        ok=actual == metadata.checksum_sha256, expected=metadata.checksum_sha256, actual=actual
    )
