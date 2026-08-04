from unittest.mock import AsyncMock

from dms_db_base import make_session_factory
from folder_service import main, repository, retention_actions
from folder_service.settings import ROOT_FOLDER_ID


async def _make_folder(session, **overrides):
    payload = {
        "name": "Ordner",
        "parent_id": ROOT_FOLDER_ID,
        "object_type_id": None,
        "attributes": {},
        "created_by": "alice",
    }
    payload.update(overrides)
    return await repository.create_folder(session, **payload)


async def test_execute_forced_deletion_removes_folder_and_writes_register_entry(session):
    folder = await _make_folder(session)

    await retention_actions.execute_forced_deletion(
        session, folder.id, reason="Frist abgelaufen", triggered_by="system:retention-poll"
    )
    await session.commit()

    try:
        await repository.get_folder(session, folder.id)
        raise AssertionError("Ordner hätte entfernt sein müssen")
    except repository.NotFoundError:
        pass

    entries = await repository.list_deletion_register(session, folder_id=folder.id)
    assert len(entries) == 1
    assert entries[0].trigger == "forced_deletion"
    assert entries[0].reason == "Frist abgelaufen"


async def test_purge_expired_trash_entry_removes_folder_and_writes_register_entry(session):
    folder = await _make_folder(session)
    document_client = AsyncMock()
    document_client.cascade_trash.return_value = []
    await repository.soft_delete_folder(
        session, folder.id, deleted_by="alice", document_client=document_client
    )

    await retention_actions.purge_expired_trash_entry(session, folder.id)
    await session.commit()

    entries = await repository.list_deletion_register(session, folder_id=folder.id)
    assert len(entries) == 1
    assert entries[0].trigger == "trash_expiry"


async def test_execute_or_defer_forced_deletion_skips_when_subtree_not_empty(engine):
    """Kein automatisches Kaskadieren der Zwangslöschung auf enthaltene
    Objekte (5.2a, seit P7-S1b, bewusste Design-Entscheidung siehe
    docs/services/folder-service.md) - ein nicht-leerer Teilbaum wird
    übersprungen, statt physisch entfernt zu werden."""
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_folder(session)
        parent = await _make_folder(session, name="Parent")
        await _make_folder(session, name="Kind", parent_id=parent.id)
        await repository.set_retention(
            session, parent.id, retention_until=None, full_deletion=True, reason="Test"
        )
        await session.commit()

    fake_approval_client = AsyncMock()
    fake_document_client = AsyncMock()
    fake_document_client.count_active.return_value = 0
    original_approval_client = getattr(main.app.state, "approval_client", None)
    original_document_client = getattr(main.app.state, "document_client", None)
    main.app.state.approval_client = fake_approval_client
    main.app.state.document_client = fake_document_client
    try:
        async with session_factory() as session:
            folder = await repository.get_folder(session, parent.id)
            await main._execute_or_defer_forced_deletion(session, folder)
    finally:
        main.app.state.approval_client = original_approval_client
        main.app.state.document_client = original_document_client

    fake_approval_client.requires_approval.assert_not_called()
    async with session_factory() as session:
        # Ordner existiert weiterhin - Zwangslöschung wurde übersprungen.
        still_there = await repository.get_folder(session, parent.id)
        assert still_there.id == parent.id


async def test_execute_or_defer_forced_deletion_skips_when_child_row_already_soft_deleted(engine):
    """Regressionstest für einen echten Bug, gefunden beim P7-S1b-Live-Smoke-
    Test: `list_active_subtree_ids` blendet bereits soft-gelöschte
    Unterordner aus (korrekt für die Nicht-leer-Prüfung gegenüber aktiven
    Objekten), aber ein soft-gelöschter Unterordner ist als DB-Zeile
    weiterhin vorhanden und referenziert den Elternordner per FK
    (`parent_id`) - `hard_delete_folder` schlug dadurch live mit einer
    `ForeignKeyViolationError` fehl, sobald der einzige Unterordner bereits
    im Papierkorb lag, statt die Zwangslöschung sauber zu überspringen.
    Fix: `has_any_child_folder_row` prüft zusätzlich OHNE `deleted_at`-Filter."""
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_folder(session)
        parent = await _make_folder(session, name="Parent")
        child = await _make_folder(session, name="Kind", parent_id=parent.id)
        document_client_for_setup = AsyncMock()
        document_client_for_setup.cascade_trash.return_value = []
        await repository.soft_delete_folder(
            session, child.id, deleted_by="alice", document_client=document_client_for_setup
        )
        await repository.set_retention(
            session, parent.id, retention_until=None, full_deletion=True, reason="Test"
        )
        await session.commit()

    fake_approval_client = AsyncMock()
    fake_document_client = AsyncMock()
    fake_document_client.count_active.return_value = 0
    original_approval_client = getattr(main.app.state, "approval_client", None)
    original_document_client = getattr(main.app.state, "document_client", None)
    main.app.state.approval_client = fake_approval_client
    main.app.state.document_client = fake_document_client
    try:
        async with session_factory() as session:
            folder = await repository.get_folder(session, parent.id)
            await main._execute_or_defer_forced_deletion(session, folder)
    finally:
        main.app.state.approval_client = original_approval_client
        main.app.state.document_client = original_document_client

    async with session_factory() as session:
        # Weder eine Exception noch eine physische Löschung - sauber übersprungen.
        still_there = await repository.get_folder(session, parent.id)
        assert still_there.id == parent.id


async def test_force_delete_approval_requested_only_once_across_poll_ticks(engine):
    """Regressionstest nach dem in P7-S1 (document-service) gefundenen
    Flush-statt-Commit-Bug (siehe PROGRESS.md) - hier von vornherein mit
    `session.commit()` implementiert, dieser Test verifiziert das explizit
    mit demselben Muster (frische Session je simuliertem Tick)."""
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_folder(session)
        folder = await _make_folder(session)
        await repository.set_retention(
            session, folder.id, retention_until=None, full_deletion=True, reason="Test"
        )
        await session.commit()

    fake_approval_client = AsyncMock()
    fake_approval_client.requires_approval.return_value = True
    fake_document_client = AsyncMock()
    fake_document_client.count_active.return_value = 0
    original_approval_client = getattr(main.app.state, "approval_client", None)
    original_document_client = getattr(main.app.state, "document_client", None)
    main.app.state.approval_client = fake_approval_client
    main.app.state.document_client = fake_document_client
    try:
        for _ in range(3):
            async with session_factory() as session:
                current = await repository.get_folder(session, folder.id)
                await main._execute_or_defer_forced_deletion(session, current)
    finally:
        main.app.state.approval_client = original_approval_client
        main.app.state.document_client = original_document_client

    assert fake_approval_client.create_request.await_count == 1

    async with session_factory() as session:
        current = await repository.get_folder(session, folder.id)
        assert current.force_delete_approval_requested_at is not None
