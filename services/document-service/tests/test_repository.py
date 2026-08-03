import uuid
from datetime import UTC, datetime, timedelta

import pytest
from document_service import repository


async def _make_document(session, **overrides):
    payload = {
        "document_id": str(uuid.uuid4()),
        "title": "Vertrag",
        "filename": "vertrag.pdf",
        "content_type": "application/pdf",
        "size_bytes": 10,
        "checksum_sha256": "a" * 64,
        "storage_object_key": "documents/x/aaaa",
        "folder_id": None,
        "object_type_id": None,
        "attributes": {},
        "created_by": "alice",
    }
    payload.update(overrides)
    return await repository.create_document(session, **payload)


async def test_create_document_creates_first_version(session):
    document = await _make_document(session)

    assert document.current_version_number == 1
    versions = await repository.list_versions(session, document.id)
    assert len(versions) == 1
    assert versions[0].version_number == 1
    assert versions[0].is_conflict is False


async def test_get_document_unknown_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_document(session, "does-not-exist")


async def test_delete_document_sets_deleted_at(session):
    document = await _make_document(session)
    deleted = await repository.delete_document(session, document.id, deleted_by="bob")
    assert deleted.deleted_at is not None


async def test_update_document_metadata_changes_title_and_attributes(session):
    document = await _make_document(session, attributes={"a": 1})

    updated = await repository.update_document_metadata(
        session, document.id, title="Neuer Titel", attributes={"a": 2}
    )

    assert updated.title == "Neuer Titel"
    assert updated.attributes == {"a": 2}


async def test_update_document_metadata_partial_update_keeps_other_field(session):
    document = await _make_document(session, title="Original", attributes={"a": 1})

    updated = await repository.update_document_metadata(
        session, document.id, title=None, attributes={"a": 2}
    )

    assert updated.title == "Original"
    assert updated.attributes == {"a": 2}


async def test_update_document_metadata_unknown_document_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.update_document_metadata(
            session, "does-not-exist", title="x", attributes=None
        )


async def test_checkin_normal_advances_current_version(session):
    document = await _make_document(session)

    version, is_conflict = await repository.checkin_version(
        session,
        document.id,
        expected_base_version_number=1,
        storage_object_key="documents/x/bbbb",
        filename="vertrag.pdf",
        content_type="application/pdf",
        size_bytes=20,
        checksum_sha256="b" * 64,
        created_by="alice",
    )

    assert is_conflict is False
    assert version.version_number == 2
    refreshed = await repository.get_document(session, document.id)
    assert refreshed.current_version_number == 2


async def test_checkin_stale_base_creates_conflict_copy(session):
    document = await _make_document(session)
    # Bob checkt normal ein, während Alice (fiktiv) noch auf Version 1 sitzt.
    await repository.checkin_version(
        session,
        document.id,
        expected_base_version_number=1,
        storage_object_key="documents/x/bbbb",
        filename="vertrag.pdf",
        content_type="application/pdf",
        size_bytes=20,
        checksum_sha256="b" * 64,
        created_by="bob",
    )

    version, is_conflict = await repository.checkin_version(
        session,
        document.id,
        expected_base_version_number=1,
        storage_object_key="documents/x/cccc",
        filename="vertrag.pdf",
        content_type="application/pdf",
        size_bytes=30,
        checksum_sha256="c" * 64,
        created_by="alice",
    )

    assert is_conflict is True
    assert version.version_number == 3
    assert "_conflict_alice_" in version.filename
    refreshed = await repository.get_document(session, document.id)
    # Konfliktkopie bewegt den Hauptversions-Zeiger nicht.
    assert refreshed.current_version_number == 2


async def test_acquire_lock_conflict(session):
    document = await _make_document(session)
    await repository.acquire_lock(
        session, document.id, locked_by="alice", session_id="s1", timeout_seconds=60
    )

    with pytest.raises(repository.LockConflictError):
        await repository.acquire_lock(
            session, document.id, locked_by="bob", session_id="s2", timeout_seconds=60
        )


async def test_same_holder_can_renew_lock(session):
    document = await _make_document(session)
    await repository.acquire_lock(
        session, document.id, locked_by="alice", session_id="s1", timeout_seconds=60
    )
    renewed = await repository.acquire_lock(
        session, document.id, locked_by="alice", session_id="s1", timeout_seconds=120
    )
    assert renewed.locked_by == "alice"


async def test_release_lock_wrong_holder_raises(session):
    document = await _make_document(session)
    await repository.acquire_lock(
        session, document.id, locked_by="alice", session_id="s1", timeout_seconds=60
    )
    with pytest.raises(repository.LockNotHeldError):
        await repository.release_lock(session, document.id, released_by="bob")


async def test_release_lock_is_idempotent_when_no_lock(session):
    document = await _make_document(session)
    await repository.release_lock(session, document.id, released_by="alice")  # darf nicht werfen


async def test_checkin_blocked_by_other_users_lock(session):
    document = await _make_document(session)
    await repository.acquire_lock(
        session, document.id, locked_by="alice", session_id="s1", timeout_seconds=60
    )

    with pytest.raises(repository.LockConflictError):
        await repository.checkin_version(
            session,
            document.id,
            expected_base_version_number=1,
            storage_object_key="documents/x/dddd",
            filename="vertrag.pdf",
            content_type="application/pdf",
            size_bytes=20,
            checksum_sha256="d" * 64,
            created_by="bob",
        )


async def test_checkin_by_lock_holder_releases_lock(session):
    document = await _make_document(session)
    await repository.acquire_lock(
        session, document.id, locked_by="alice", session_id="s1", timeout_seconds=60
    )

    await repository.checkin_version(
        session,
        document.id,
        expected_base_version_number=1,
        storage_object_key="documents/x/eeee",
        filename="vertrag.pdf",
        content_type="application/pdf",
        size_bytes=20,
        checksum_sha256="e" * 64,
        created_by="alice",
    )

    assert await repository.get_lock(session, document.id) is None


async def test_force_release_lock_returns_original_holder(session):
    document = await _make_document(session)
    await repository.acquire_lock(
        session, document.id, locked_by="alice", session_id="s1", timeout_seconds=60
    )

    original = await repository.force_release_lock(session, document.id)

    assert original.locked_by == "alice"
    assert await repository.get_lock(session, document.id) is None


async def test_force_unlock_then_conflicting_checkin_becomes_conflict_copy(session):
    """Der zentrale Force-Unlock-Ablauf aus 4.2: Alice sperrt, Admin hebt die
    Sperre administrativ auf, Bob checkt regulär ein (wird neue Hauptversion),
    Alice versucht danach ebenfalls einzuchecken - ihr Stand basiert auf der
    inzwischen überholten Version 1 und landet als Konfliktkopie, statt Bobs
    Version stillschweigend zu überschreiben."""
    document = await _make_document(session)
    await repository.acquire_lock(
        session, document.id, locked_by="alice", session_id="s1", timeout_seconds=60
    )

    await repository.force_release_lock(session, document.id)

    await repository.checkin_version(
        session,
        document.id,
        expected_base_version_number=1,
        storage_object_key="documents/x/ffff",
        filename="vertrag.pdf",
        content_type="application/pdf",
        size_bytes=20,
        checksum_sha256="f" * 64,
        created_by="bob",
    )

    version, is_conflict = await repository.checkin_version(
        session,
        document.id,
        expected_base_version_number=1,
        storage_object_key="documents/x/gggg",
        filename="vertrag.pdf",
        content_type="application/pdf",
        size_bytes=25,
        checksum_sha256="g" * 64,
        created_by="alice",
    )

    assert is_conflict is True
    assert "_conflict_alice_" in version.filename
    refreshed = await repository.get_document(session, document.id)
    assert refreshed.current_version_number == 2  # Bobs Version bleibt aktuell


async def test_get_upload_config_defaults_to_no_restriction(session):
    config = await repository.get_upload_config(session)

    assert config.allowed_content_types == []


async def test_update_upload_config_persists_values(session):
    updated = await repository.update_upload_config(
        session, allowed_content_types=["application/pdf", "text/plain"]
    )
    await session.commit()

    assert updated.allowed_content_types == ["application/pdf", "text/plain"]

    fetched = await repository.get_upload_config(session)
    assert fetched.allowed_content_types == ["application/pdf", "text/plain"]


# --- Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) ---------


async def test_set_retention_persists_fields_and_resets_bookkeeping(session):
    document = await _make_document(session)
    document.deletion_reminder_sent_at = datetime.now(UTC)
    document.force_delete_approval_requested_at = datetime.now(UTC)
    await session.flush()
    retention_until = datetime.now(UTC) + timedelta(days=30)

    updated = await repository.set_retention(
        session,
        document.id,
        retention_until=retention_until,
        full_deletion=True,
        reason="Aufbewahrungsfrist abgelaufen",
        notify_email="records@example.com",
    )

    assert updated.retention_until == retention_until
    assert updated.full_deletion is True
    assert updated.pending_deletion_reason == "Aufbewahrungsfrist abgelaufen"
    assert updated.reminder_notify_email == "records@example.com"
    # Neu terminiert - alte Buchführung ist hinfällig.
    assert updated.deletion_reminder_sent_at is None
    assert updated.force_delete_approval_requested_at is None


async def test_restore_document_within_period_clears_deleted_at(session):
    document = await _make_document(session)
    await repository.delete_document(session, document.id, deleted_by="alice")

    restored = await repository.restore_document(session, document.id)

    assert restored.deleted_at is None


async def test_restore_document_not_deleted_raises(session):
    document = await _make_document(session)

    with pytest.raises(repository.NotDeletedError):
        await repository.restore_document(session, document.id)


async def test_restore_document_after_period_expired_raises(session):
    document = await _make_document(session)
    await repository.update_trash_config(session, restore_period_days=1)
    deleted = await repository.delete_document(session, document.id, deleted_by="alice")
    deleted.deleted_at = datetime.now(UTC) - timedelta(days=2)
    await session.flush()

    with pytest.raises(repository.RestorePeriodExpiredError):
        await repository.restore_document(session, document.id)


async def test_list_deleted_documents_only_shows_soft_deleted(session):
    kept = await _make_document(session, folder_id="root")
    deleted = await _make_document(session, folder_id="root")
    await repository.delete_document(session, deleted.id, deleted_by="alice")

    result = await repository.list_deleted_documents(session, "root")

    ids = [d.id for d in result]
    assert deleted.id in ids
    assert kept.id not in ids


async def test_hard_delete_document_removes_document_and_versions(session):
    document = await _make_document(session)

    await repository.hard_delete_document(session, document.id)

    with pytest.raises(repository.NotFoundError):
        await repository.get_document(session, document.id)


async def test_hard_delete_document_removes_legal_hold_history(session):
    """Auch eine bereits aufgehobene (historische) Legal-Hold-Zeile referenziert
    das Dokument per FK - die harte Löschung muss sie mitentfernen, sonst
    verletzt sie die FK-Constraint."""
    document = await _make_document(session)
    hold = await repository.create_legal_hold(session, document.id, set_by="alice", reason=None)
    await repository.release_legal_hold(session, hold.id, released_by="alice")

    await repository.hard_delete_document(session, document.id)

    with pytest.raises(repository.NotFoundError):
        await repository.get_document(session, document.id)


async def test_create_and_release_legal_hold(session):
    document = await _make_document(session)

    hold = await repository.create_legal_hold(
        session, document.id, set_by="alice", reason="Rechtsstreit"
    )
    assert await repository.has_active_hold(session, document.id) is True

    released = await repository.release_legal_hold(session, hold.id, released_by="bob")
    assert released.released_by == "bob"
    assert await repository.has_active_hold(session, document.id) is False


async def test_release_already_released_hold_raises(session):
    document = await _make_document(session)
    hold = await repository.create_legal_hold(session, document.id, set_by="alice", reason=None)
    await repository.release_legal_hold(session, hold.id, released_by="alice")

    with pytest.raises(repository.AlreadyReleasedError):
        await repository.release_legal_hold(session, hold.id, released_by="alice")


async def test_create_legal_hold_for_unknown_document_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.create_legal_hold(session, "does-not-exist", set_by="alice", reason=None)


async def test_list_holds_active_only_filters_released(session):
    document = await _make_document(session)
    released_hold = await repository.create_legal_hold(
        session, document.id, set_by="a", reason=None
    )
    await repository.release_legal_hold(session, released_hold.id, released_by="a")
    active_hold = await repository.create_legal_hold(session, document.id, set_by="b", reason=None)

    all_holds = await repository.list_holds(session, document.id)
    active_only = await repository.list_holds(session, document.id, active_only=True)

    assert {h.id for h in all_holds} == {released_hold.id, active_hold.id}
    assert {h.id for h in active_only} == {active_hold.id}


async def test_deletion_register_entry_roundtrip(session):
    entry = await repository.create_deletion_register_entry(
        session, "doc-1", trigger="forced_deletion", reason="Frist abgelaufen", triggered_by="alice"
    )

    all_entries = await repository.list_deletion_register(session)
    filtered = await repository.list_deletion_register(session, document_id="doc-1")
    other = await repository.list_deletion_register(session, document_id="doc-2")

    assert entry.id in [e.id for e in all_entries]
    assert [e.id for e in filtered] == [entry.id]
    assert other == []


async def test_retention_config_defaults_and_update(session):
    default = await repository.get_retention_config(session)
    assert default.deletion_reason_required is False
    assert default.reminder_lead_days is None

    updated = await repository.update_retention_config(
        session, deletion_reason_required=True, reminder_lead_days=7
    )
    assert updated.deletion_reason_required is True
    assert updated.reminder_lead_days == 7


async def test_trash_config_defaults_and_update(session):
    default = await repository.get_trash_config(session)
    assert default.restore_period_days == 30

    updated = await repository.update_trash_config(session, restore_period_days=60)
    assert updated.restore_period_days == 60


async def test_list_due_for_reminder_respects_lead_days_and_hold(session):
    due_soon = await _make_document(session)
    due_soon.retention_until = datetime.now(UTC) + timedelta(days=2)
    await session.flush()

    too_far = await _make_document(session)
    too_far.retention_until = datetime.now(UTC) + timedelta(days=30)
    await session.flush()

    on_hold = await _make_document(session)
    on_hold.retention_until = datetime.now(UTC) + timedelta(days=2)
    await session.flush()
    await repository.create_legal_hold(session, on_hold.id, set_by="alice", reason=None)

    due = await repository.list_due_for_reminder(session, lead_days=7)

    ids = {d.id for d in due}
    assert due_soon.id in ids
    assert too_far.id not in ids
    assert on_hold.id not in ids


async def test_list_due_for_reminder_skips_already_notified(session):
    document = await _make_document(session)
    document.retention_until = datetime.now(UTC) + timedelta(days=1)
    document.deletion_reminder_sent_at = datetime.now(UTC)
    await session.flush()

    due = await repository.list_due_for_reminder(session, lead_days=7)

    assert document.id not in {d.id for d in due}


async def test_list_due_for_retention_action_excludes_hold_and_future(session):
    due = await _make_document(session)
    due.retention_until = datetime.now(UTC) - timedelta(days=1)
    await session.flush()

    future = await _make_document(session)
    future.retention_until = datetime.now(UTC) + timedelta(days=1)
    await session.flush()

    on_hold = await _make_document(session)
    on_hold.retention_until = datetime.now(UTC) - timedelta(days=1)
    await session.flush()
    await repository.create_legal_hold(session, on_hold.id, set_by="alice", reason=None)

    result = await repository.list_due_for_retention_action(session)

    ids = {d.id for d in result}
    assert due.id in ids
    assert future.id not in ids
    assert on_hold.id not in ids


async def test_list_expired_trash_excludes_hold_and_not_yet_expired(session):
    expired = await _make_document(session)
    await repository.delete_document(session, expired.id, deleted_by="alice")
    expired.deleted_at = datetime.now(UTC) - timedelta(days=40)
    await session.flush()

    recent = await _make_document(session)
    await repository.delete_document(session, recent.id, deleted_by="alice")

    on_hold = await _make_document(session)
    await repository.delete_document(session, on_hold.id, deleted_by="alice")
    on_hold.deleted_at = datetime.now(UTC) - timedelta(days=40)
    await session.flush()
    await repository.create_legal_hold(session, on_hold.id, set_by="alice", reason=None)

    result = await repository.list_expired_trash(session, restore_period_days=30)

    ids = {d.id for d in result}
    assert expired.id in ids
    assert recent.id not in ids
    assert on_hold.id not in ids
