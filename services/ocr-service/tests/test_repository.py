import uuid

import pytest
from ocr_service import repository


async def test_upsert_then_get(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    created = await repository.upsert_ocr_result(
        session,
        document_id=document_id,
        version_number=1,
        status="ready",
        engine="native_text_layer",
        average_confidence=100.0,
        full_text="Hallo Welt",
        pages=[{"page_number": 1, "width": 100, "height": 100, "words": []}],
        page_image_storage_key=f"ocr/{document_id}/1/page-1.png",
        error_message=None,
    )
    await session.commit()

    fetched = await repository.get_ocr_result(session, created.id)
    assert fetched.id == f"{document_id}:1"
    assert fetched.status == "ready"
    assert fetched.full_text == "Hallo Welt"


async def test_get_unknown_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_ocr_result(session, "unknown:1")


async def test_upsert_overwrites_same_key(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await repository.upsert_ocr_result(
        session,
        document_id=document_id,
        version_number=1,
        status="failed",
        engine="",
        average_confidence=0.0,
        full_text="",
        pages=[],
        page_image_storage_key=None,
        error_message="kaputt",
    )
    await session.commit()

    updated = await repository.upsert_ocr_result(
        session,
        document_id=document_id,
        version_number=1,
        status="ready",
        engine="tesseract",
        average_confidence=88.0,
        full_text="erkannter Text",
        pages=[{"page_number": 1, "width": 50, "height": 50, "words": []}],
        page_image_storage_key=None,
        error_message=None,
    )
    await session.commit()

    assert updated.id == f"{document_id}:1"
    assert updated.status == "ready"
    assert updated.error_message is None

    all_for_doc = await repository.list_ocr_results(session, document_id=document_id)
    assert len(all_for_doc) == 1


async def test_get_config_creates_default_row_on_first_access(session):
    config = await repository.get_config(session)
    await session.commit()

    assert config.max_word_count is None
    assert config.batch_size == repository.DEFAULT_BATCH_SIZE


async def test_get_config_is_idempotent(session):
    first = await repository.get_config(session)
    await session.commit()
    second = await repository.get_config(session)
    await session.commit()

    assert first.id == second.id == 1


async def test_update_config_persists_values(session):
    updated = await repository.update_config(session, max_word_count=5000, batch_size=8)
    await session.commit()

    assert updated.max_word_count == 5000
    assert updated.batch_size == 8

    fetched = await repository.get_config(session)
    assert fetched.max_word_count == 5000
    assert fetched.batch_size == 8


async def test_update_config_can_clear_max_word_count(session):
    await repository.update_config(session, max_word_count=100, batch_size=4)
    await session.commit()

    cleared = await repository.update_config(session, max_word_count=None, batch_size=4)
    await session.commit()

    assert cleared.max_word_count is None


async def test_list_ocr_results_filters_by_version(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    for version in (1, 2):
        await repository.upsert_ocr_result(
            session,
            document_id=document_id,
            version_number=version,
            status="ready",
            engine="tesseract",
            average_confidence=90.0,
            full_text="Text",
            pages=[],
            page_image_storage_key=None,
            error_message=None,
        )
    await session.commit()

    only_v2 = await repository.list_ocr_results(session, document_id=document_id, version_number=2)
    assert len(only_v2) == 1
    assert only_v2[0].version_number == 2

    both = await repository.list_ocr_results(session, document_id=document_id)
    assert len(both) == 2
