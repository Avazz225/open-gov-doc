from datetime import UTC, datetime, timedelta

import pytest
from archival_service import repository


async def test_create_and_get_transfer(session):
    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    fetched = await repository.get_transfer(session, transfer.id)

    assert fetched.document_id == "doc-1"
    assert fetched.status == "pending"


async def test_get_transfer_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_transfer(session, "does-not-exist")


async def test_get_active_transfer_for_document_finds_active_status(session):
    transfer = await repository.create_transfer(session, "doc-2")
    await session.commit()

    found = await repository.get_active_transfer_for_document(session, "doc-2")

    assert found is not None
    assert found.id == transfer.id


async def test_get_active_transfer_for_document_ignores_terminal_status(session):
    transfer = await repository.create_transfer(session, "doc-3")
    await repository.mark_failed(session, transfer, error_message="boom")
    await session.commit()

    found = await repository.get_active_transfer_for_document(session, "doc-3")

    assert found is None


async def test_list_active_transfers_excludes_released_and_failed(session):
    active = await repository.create_transfer(session, "doc-4")
    released = await repository.create_transfer(session, "doc-5")
    await repository.update_status(
        session, released, status="released", released_at=datetime.now(UTC)
    )
    failed = await repository.create_transfer(session, "doc-6")
    await repository.mark_failed(session, failed, error_message="boom")
    await session.commit()

    active_ids = {t.id for t in await repository.list_active_transfers(session)}

    assert active_ids == {active.id}


async def test_list_due_for_dehydration_filters_by_delay(session):
    now = datetime.now(UTC)
    overdue = await repository.create_transfer(session, "doc-7")
    await repository.update_status(
        session, overdue, status="released", released_at=now - timedelta(days=31)
    )
    recent = await repository.create_transfer(session, "doc-8")
    await repository.update_status(
        session, recent, status="released", released_at=now - timedelta(days=1)
    )
    await session.commit()

    due_ids = {t.id for t in await repository.list_due_for_dehydration(session, delay_days=30)}

    assert due_ids == {overdue.id}


async def test_mark_failed_sets_status_and_error(session):
    transfer = await repository.create_transfer(session, "doc-9")

    await repository.mark_failed(session, transfer, error_message="Konvertierung fehlgeschlagen")

    assert transfer.status == "failed"
    assert transfer.error_message == "Konvertierung fehlgeschlagen"


async def test_list_transfers_filters_by_status(session):
    await repository.create_transfer(session, "doc-10")
    released = await repository.create_transfer(session, "doc-11")
    await repository.update_status(
        session, released, status="released", released_at=datetime.now(UTC)
    )
    await session.commit()

    released_only = await repository.list_transfers(session, status="released")

    assert [t.document_id for t in released_only] == ["doc-11"]
