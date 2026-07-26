import uuid

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
