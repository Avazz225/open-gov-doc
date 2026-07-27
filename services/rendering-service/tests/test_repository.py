import uuid

import pytest
from rendering_service import repository


async def test_upsert_then_get(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    created = await repository.upsert_rendition(
        session,
        document_id=document_id,
        version_number=1,
        rendition_type="thumbnail",
        source_filename="foto.png",
        source_content_type="image/png",
        target_filename="foto_thumbnail.png",
        target_content_type="image/png",
        size_bytes=1234,
        storage_object_key=f"renditions/{document_id}/1/thumbnail",
        status="ready",
        error_message=None,
    )
    await session.commit()

    fetched = await repository.get_rendition(session, created.id)
    assert fetched.id == f"{document_id}:1:thumbnail"
    assert fetched.status == "ready"
    assert fetched.size_bytes == 1234


async def test_get_unknown_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_rendition(session, "unknown:1:thumbnail")


async def test_upsert_overwrites_same_key(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await repository.upsert_rendition(
        session,
        document_id=document_id,
        version_number=1,
        rendition_type="pdf_archive",
        source_filename="a.pdf",
        source_content_type="application/pdf",
        target_filename="",
        target_content_type="",
        size_bytes=0,
        storage_object_key="",
        status="failed",
        error_message="kaputt",
    )
    await session.commit()

    updated = await repository.upsert_rendition(
        session,
        document_id=document_id,
        version_number=1,
        rendition_type="pdf_archive",
        source_filename="a.pdf",
        source_content_type="application/pdf",
        target_filename="a_archiv.pdf",
        target_content_type="application/pdf",
        size_bytes=999,
        storage_object_key=f"renditions/{document_id}/1/pdf_archive",
        status="ready",
        error_message=None,
    )
    await session.commit()

    assert updated.id == f"{document_id}:1:pdf_archive"
    assert updated.status == "ready"
    assert updated.error_message is None

    all_for_doc = await repository.list_renditions(session, document_id=document_id)
    assert len(all_for_doc) == 1


async def test_list_renditions_filters_by_version(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    for version in (1, 2):
        await repository.upsert_rendition(
            session,
            document_id=document_id,
            version_number=version,
            rendition_type="thumbnail",
            source_filename="foto.png",
            source_content_type="image/png",
            target_filename="foto_thumbnail.png",
            target_content_type="image/png",
            size_bytes=10,
            storage_object_key=f"renditions/{document_id}/{version}/thumbnail",
            status="ready",
            error_message=None,
        )
    await session.commit()

    only_v2 = await repository.list_renditions(session, document_id=document_id, version_number=2)
    assert len(only_v2) == 1
    assert only_v2[0].version_number == 2

    both = await repository.list_renditions(session, document_id=document_id)
    assert len(both) == 2
