from datetime import UTC, datetime

import pytest
from reporting_service import repository


async def test_record_and_aggregate_document_volume_by_day(session):
    await repository.record_document_created(
        session,
        document_id="doc-1",
        folder_id="folder-a",
        occurred_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
    )
    await repository.record_document_created(
        session,
        document_id="doc-2",
        folder_id="folder-a",
        occurred_at=datetime(2026, 1, 1, 14, 0, tzinfo=UTC),
    )
    await repository.record_document_created(
        session,
        document_id="doc-3",
        folder_id="folder-b",
        occurred_at=datetime(2026, 1, 2, 9, 0, tzinfo=UTC),
    )

    rows = await repository.get_document_volume(session, group_by="day")

    assert ("2026-01-01", "folder-a", 2) in rows
    assert ("2026-01-02", "folder-b", 1) in rows


async def test_document_volume_filters_by_folder(session):
    await repository.record_document_created(
        session,
        document_id="doc-1",
        folder_id="folder-a",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await repository.record_document_created(
        session,
        document_id="doc-2",
        folder_id="folder-b",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    rows = await repository.get_document_volume(session, folder_id="folder-a", group_by="day")

    assert len(rows) == 1
    assert rows[0][1] == "folder-a"


async def test_document_volume_filters_by_time_window(session):
    await repository.record_document_created(
        session, document_id="doc-1", folder_id=None, occurred_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    await repository.record_document_created(
        session, document_id="doc-2", folder_id=None, occurred_at=datetime(2026, 6, 1, tzinfo=UTC)
    )

    rows = await repository.get_document_volume(
        session,
        since=datetime(2026, 3, 1, tzinfo=UTC),
        until=datetime(2026, 9, 1, tzinfo=UTC),
        group_by="month",
    )

    assert len(rows) == 1
    assert rows[0][0] == "2026-06"


async def test_document_volume_groups_by_month(session):
    await repository.record_document_created(
        session, document_id="doc-1", folder_id=None, occurred_at=datetime(2026, 1, 5, tzinfo=UTC)
    )
    await repository.record_document_created(
        session, document_id="doc-2", folder_id=None, occurred_at=datetime(2026, 1, 20, tzinfo=UTC)
    )

    rows = await repository.get_document_volume(session, group_by="month")

    assert rows == [("2026-01", None, 2)]


@pytest.mark.parametrize(
    "frequency,expected",
    [
        ("daily", datetime(2026, 1, 2, 10, 0, tzinfo=UTC)),
        ("weekly", datetime(2026, 1, 8, 10, 0, tzinfo=UTC)),
    ],
)
def test_advance_next_run_daily_weekly(frequency, expected):
    current = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert repository.advance_next_run(current, frequency) == expected


def test_advance_next_run_monthly_handles_day_overflow():
    # 31. Januar -> Februar hat nur 28 Tage in 2026 (kein Schaltjahr).
    current = datetime(2026, 1, 31, 10, 0, tzinfo=UTC)
    assert repository.advance_next_run(current, "monthly") == datetime(
        2026, 2, 28, 10, 0, tzinfo=UTC
    )


def test_advance_next_run_monthly_rolls_over_year():
    current = datetime(2026, 12, 15, tzinfo=UTC)
    assert repository.advance_next_run(current, "monthly") == datetime(2027, 1, 15, tzinfo=UTC)


async def test_create_list_delete_schedule(session):
    schedule = await repository.create_schedule(
        session,
        report_type="document_volume",
        format="csv",
        frequency="daily",
        recipient_email="admin@example.invalid",
        filters={},
    )

    listed = await repository.list_schedules(session)
    assert [s.id for s in listed] == [schedule.id]

    await repository.delete_schedule(session, schedule.id)
    assert await repository.list_schedules(session) == []


async def test_delete_unknown_schedule_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.delete_schedule(session, "unknown-id")


async def test_list_due_schedules_only_returns_due_ones(session):
    due = await repository.create_schedule(
        session,
        report_type="storage_usage",
        format="pdf",
        frequency="daily",
        recipient_email="a@example.invalid",
        filters={},
    )
    not_due = await repository.create_schedule(
        session,
        report_type="storage_usage",
        format="pdf",
        frequency="daily",
        recipient_email="b@example.invalid",
        filters={},
    )
    not_due.next_run_at = datetime(2099, 1, 1, tzinfo=UTC)
    await session.flush()

    result = await repository.list_due_schedules(session, now=datetime.now(UTC))

    assert [s.id for s in result] == [due.id]


async def test_mark_schedule_run_advances_next_run_and_sets_last_run(session):
    schedule = await repository.create_schedule(
        session,
        report_type="storage_usage",
        format="csv",
        frequency="daily",
        recipient_email="a@example.invalid",
        filters={},
    )
    schedule.next_run_at = datetime(2026, 1, 1, tzinfo=UTC)
    await session.flush()

    ran_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    await repository.mark_schedule_run(session, schedule, ran_at=ran_at)

    assert schedule.last_run_at == ran_at
    assert schedule.next_run_at == datetime(2026, 1, 2, tzinfo=UTC)


async def test_create_and_get_report_run(session):
    run = await repository.create_report_run(
        session,
        schedule_id=None,
        report_type="storage_usage",
        format="csv",
        storage_object_key="reports/x/y.csv",
        content_type="text/csv",
    )

    fetched = await repository.get_report_run(session, run.id)
    assert fetched.storage_object_key == "reports/x/y.csv"


async def test_get_unknown_report_run_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_report_run(session, "unknown-id")
