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
        page_image_storage_key=f"ocr/{document_id}/1/page",
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
    # Standardmäßig nur PDFs (Nutzer-Feedback) - Bilder erfordern eine
    # bewusste Admin-Freigabe über PUT /config.
    assert config.allowed_content_types == ["application/pdf"]


async def test_get_config_is_idempotent(session):
    first = await repository.get_config(session)
    await session.commit()
    second = await repository.get_config(session)
    await session.commit()

    assert first.id == second.id == 1


async def test_update_config_persists_values(session):
    updated = await repository.update_config(
        session, max_word_count=5000, batch_size=8, allowed_content_types=["image/tiff"]
    )
    await session.commit()

    assert updated.max_word_count == 5000
    assert updated.batch_size == 8
    assert updated.allowed_content_types == ["image/tiff"]

    fetched = await repository.get_config(session)
    assert fetched.max_word_count == 5000
    assert fetched.batch_size == 8
    assert fetched.allowed_content_types == ["image/tiff"]


async def test_update_config_can_clear_max_word_count(session):
    await repository.update_config(
        session, max_word_count=100, batch_size=4, allowed_content_types=[]
    )
    await session.commit()

    cleared = await repository.update_config(
        session, max_word_count=None, batch_size=4, allowed_content_types=[]
    )
    await session.commit()

    assert cleared.max_word_count is None


async def test_record_failure_below_max_attempts_stays_failed_and_schedules_retry(session):
    """Post-Roadmap Phase 20 Session 4 (ADR 0080): unterhalb von
    `max_attempts` bleibt `status="failed"` (retry-fähig) mit gesetztem
    `next_retry_at`, statt sofort terminal zu werden."""
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session,
        document_id=document_id,
        version_number=1,
        engine="tesseract",
        error="kaputt",
        max_attempts=5,
    )
    await session.commit()

    assert result.status == "failed"
    assert result.attempts == 1
    assert result.next_retry_at is not None


async def test_record_failure_reaches_failed_permanent_at_max_attempts(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session,
        document_id=document_id,
        version_number=1,
        engine="tesseract",
        error="kaputt",
        max_attempts=1,
    )
    await session.commit()

    assert result.status == "failed_permanent"
    assert result.attempts == 1
    assert result.next_retry_at is None


async def test_record_failure_increments_attempts_on_repeated_failure(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await repository.record_failure(
        session,
        document_id=document_id,
        version_number=1,
        engine="tesseract",
        error="kaputt 1",
        max_attempts=5,
    )
    await session.commit()

    second = await repository.record_failure(
        session,
        document_id=document_id,
        version_number=1,
        engine="tesseract",
        error="kaputt 2",
        max_attempts=5,
    )
    await session.commit()

    assert second.attempts == 2
    assert second.status == "failed"


async def test_reset_for_retry_clears_attempts_error_and_next_retry_at(session):
    """Post-Roadmap Phase 20 Session 4 (ADR 0080) - Regressionstest für einen
    bei der Live-Verifikation gefundenen echten Bug: ohne diesen Reset würde
    ein erneuter Fehlschlag nach manuellem Retry von der bereits erschöpften
    `attempts`-Zahl weiterzählen und sofort wieder `failed_permanent`
    erreichen, egal wie viele Versuche `max_attempts` eigentlich erlaubt."""
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=1
    )
    await session.commit()
    assert result.status == "failed_permanent"
    assert result.attempts == 1

    await repository.reset_for_retry(session, result)
    await session.commit()

    assert result.attempts == 0
    assert result.error_message is None
    assert result.next_retry_at is None
    # `status` bleibt bewusst unberührt - der Aufrufer (main.py) startet
    # danach einen echten Verarbeitungsversuch, der `status` neu setzt.
    assert result.status == "failed_permanent"


async def test_list_due_for_retry_excludes_ready_skipped_and_failed_permanent(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    retryable = await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=5
    )
    retryable.next_retry_at = None  # sofort faellig fuer diesen Test
    await session.commit()

    await repository.upsert_ocr_result(
        session,
        document_id=f"doc-{uuid.uuid4().hex[:8]}",
        version_number=1,
        status="ready",
        engine="tesseract",
        average_confidence=90.0,
        full_text="Text",
        pages=[],
        page_image_storage_key=None,
        error_message=None,
    )
    permanent = await repository.record_failure(
        session,
        document_id=f"doc-{uuid.uuid4().hex[:8]}",
        version_number=1,
        engine="",
        error="e",
        max_attempts=1,
    )
    await session.commit()

    due_ids = {r.id for r in await repository.list_due_for_retry(session)}

    assert due_ids == {retryable.id}
    assert permanent.id not in due_ids


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


async def test_list_ocr_results_without_document_id_filters_by_status(session):
    """Post-Roadmap Phase 20 Session 7: ``document_id`` ist jetzt optional -
    Grundlage für die dokumentübergreifende Admin-UI-Sicht auf dauerhaft
    fehlgeschlagene OCR-Ergebnisse."""
    doc_a = f"doc-{uuid.uuid4().hex[:8]}"
    doc_b = f"doc-{uuid.uuid4().hex[:8]}"
    await repository.upsert_ocr_result(
        session,
        document_id=doc_a,
        version_number=1,
        status="failed_permanent",
        engine="tesseract",
        average_confidence=0.0,
        full_text="",
        pages=[],
        page_image_storage_key=None,
        error_message="x",
    )
    await repository.upsert_ocr_result(
        session,
        document_id=doc_b,
        version_number=1,
        status="ready",
        engine="tesseract",
        average_confidence=90.0,
        full_text="Text",
        pages=[],
        page_image_storage_key=None,
        error_message=None,
    )
    await session.commit()

    failed = await repository.list_ocr_results(session, status="failed_permanent")
    failed_ids = {r.document_id for r in failed}
    assert doc_a in failed_ids
    assert doc_b not in failed_ids
