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
    await repository.mark_failed(session, transfer, error_message="boom", max_attempts=1)
    await session.commit()

    found = await repository.get_active_transfer_for_document(session, "doc-3")

    assert found is None


async def test_list_active_transfers_excludes_released_and_failed_permanent(session):
    active = await repository.create_transfer(session, "doc-4")
    released = await repository.create_transfer(session, "doc-5")
    await repository.update_status(
        session, released, status="released", released_at=datetime.now(UTC)
    )
    failed = await repository.create_transfer(session, "doc-6")
    await repository.mark_failed(session, failed, error_message="boom", max_attempts=1)
    await session.commit()

    active_ids = {t.id for t in await repository.list_active_transfers(session)}

    assert active_ids == {active.id}


async def test_list_active_transfers_excludes_transfers_still_in_backoff(session):
    """Post-Roadmap Phase 20 Session 2 (ADR 0078): ein Transfer mit einem
    noch in der Zukunft liegenden `next_retry_at` bleibt in seiner aktiven
    Phase, wird aber erst nach Ablauf des Backoffs wieder aufgegriffen."""
    active = await repository.create_transfer(session, "doc-4b")
    waiting = await repository.create_transfer(session, "doc-4c")
    await repository.mark_failed(session, waiting, error_message="boom", max_attempts=5)
    await session.commit()

    assert waiting.status == "pending"  # Phase bleibt erhalten, kein Terminalstatus
    assert waiting.next_retry_at is not None

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


async def test_mark_failed_below_max_attempts_keeps_phase_and_schedules_retry(session):
    """Post-Roadmap Phase 20 Session 2 (ADR 0078): ein Fehlschlag unterhalb
    von `max_attempts` verlaesst NICHT die aktuelle Phase (`status` bleibt
    `pending`) - nur `attempts`/`next_retry_at`/`error_message` aendern
    sich, damit der naechste Poll-Tick es per Backoff erneut versucht."""
    transfer = await repository.create_transfer(session, "doc-9")

    await repository.mark_failed(
        session, transfer, error_message="Konvertierung fehlgeschlagen", max_attempts=5
    )

    assert transfer.status == "pending"
    assert transfer.attempts == 1
    assert transfer.next_retry_at is not None
    assert transfer.error_message == "Konvertierung fehlgeschlagen"


async def test_mark_failed_reaches_failed_permanent_at_max_attempts(session):
    transfer = await repository.create_transfer(session, "doc-9b")

    await repository.mark_failed(
        session, transfer, error_message="Konvertierung fehlgeschlagen", max_attempts=1
    )

    assert transfer.status == "failed_permanent"
    assert transfer.attempts == 1
    assert transfer.next_retry_at is None
    assert transfer.error_message == "Konvertierung fehlgeschlagen"


async def test_reset_for_retry_restarts_a_failed_permanent_transfer(session):
    transfer = await repository.create_transfer(session, "doc-9c")
    await repository.mark_failed(session, transfer, error_message="boom", max_attempts=1)
    assert transfer.status == "failed_permanent"

    await repository.reset_for_retry(session, transfer)

    assert transfer.status == "pending"
    assert transfer.attempts == 0
    assert transfer.next_retry_at is None
    assert transfer.error_message is None


async def test_list_transfers_filters_by_status(session):
    await repository.create_transfer(session, "doc-10")
    released = await repository.create_transfer(session, "doc-11")
    await repository.update_status(
        session, released, status="released", released_at=datetime.now(UTC)
    )
    await session.commit()

    released_only = await repository.list_transfers(session, status="released")

    assert [t.document_id for t in released_only] == ["doc-11"]
