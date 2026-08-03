import os
import uuid
from unittest.mock import AsyncMock

from dms_db_base import make_session_factory
from document_service import main, repository, retention_actions
from document_service.storage_client import StorageClient

STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")


async def _upload_and_create_document(session, storage: StorageClient, *, content: bytes) -> str:
    document_id = str(uuid.uuid4())
    key = f"documents/{document_id}/{uuid.uuid4().hex}"
    await storage.upload(key, content, "text/plain")
    document = await repository.create_document(
        session,
        document_id=document_id,
        title="Zwangslöschungs-Testdokument",
        filename="test.txt",
        content_type="text/plain",
        size_bytes=len(content),
        checksum_sha256="x" * 64,
        storage_object_key=key,
        folder_id=None,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    await session.commit()
    return document.id


async def test_execute_forced_deletion_removes_storage_content_and_document(session):
    storage = StorageClient(STORAGE_SERVICE_URL)
    try:
        document_id = await _upload_and_create_document(session, storage, content=b"geheim")
        version = await repository.get_current_version(session, document_id)
        key = version.storage_object_key

        assert await storage.download(key) == b"geheim"

        await retention_actions.execute_forced_deletion(
            session,
            storage,
            document_id,
            reason="Frist abgelaufen",
            triggered_by="system:retention-poll",
            governance_bypass_role="dms-admin",
        )
        await session.commit()

        # Dokument ist vollständig aus dem Schema entfernt.
        try:
            await repository.get_document(session, document_id)
            raise AssertionError("Dokument hätte entfernt sein müssen")
        except repository.NotFoundError:
            pass

        # Storage-Inhalt ist tatsächlich weg, nicht nur die Metadaten-Zeile.
        try:
            await storage.download(key)
            raise AssertionError("Storage-Inhalt hätte entfernt sein müssen")
        except Exception:
            pass

        entries = await repository.list_deletion_register(session, document_id=document_id)
        assert len(entries) == 1
        assert entries[0].trigger == "forced_deletion"
        assert entries[0].reason == "Frist abgelaufen"
    finally:
        await storage.close()


async def test_purge_expired_trash_entry_removes_storage_content_and_document(session):
    storage = StorageClient(STORAGE_SERVICE_URL)
    try:
        document_id = await _upload_and_create_document(session, storage, content=b"alter kram")
        await repository.delete_document(session, document_id, deleted_by="alice")
        await session.commit()

        purged = await retention_actions.purge_expired_trash_entry(session, storage, document_id)
        await session.commit()

        assert purged is True
        try:
            await repository.get_document(session, document_id)
            raise AssertionError("Dokument hätte entfernt sein müssen")
        except repository.NotFoundError:
            pass

        entries = await repository.list_deletion_register(session, document_id=document_id)
        assert len(entries) == 1
        assert entries[0].trigger == "trash_expiry"
        assert entries[0].reason is None
    finally:
        await storage.close()


async def test_force_delete_approval_requested_only_once_across_poll_ticks(engine):
    """Regressionstest für einen echten Bug, gefunden beim P7-S1-Live-Smoke-
    Test: `_execute_or_defer_forced_deletion` flushte `force_delete_approval_
    requested_at` nur, statt es zu committen - beim Schließen der
    Poll-Loop-eigenen Session (eine frische Session je Tick, siehe
    `_retention_poll_loop`) ging die Änderung dadurch wieder verloren,
    wodurch JEDER weitere Tick einen weiteren, doppelten Freigabe-Request
    anlegte (live gegen den echten Stack drei Duplikate für dasselbe
    Dokument beobachtet). Nutzt bewusst je Tick eine eigene, frische Session
    (wie der echte Poll-Loop) statt einer einzigen wiederverwendeten Session
    - Letzteres hätte den Bug über die Identity Map maskiert, ohne ihn
    tatsächlich zu committen."""
    session_factory = make_session_factory(engine)
    storage = StorageClient(STORAGE_SERVICE_URL)
    try:
        async with session_factory() as session:
            document_id = await _upload_and_create_document(session, storage, content=b"geheim")
            await repository.set_retention(
                session,
                document_id,
                retention_until=None,
                full_deletion=True,
                reason="Test",
            )
            await session.commit()

        fake_approval_client = AsyncMock()
        fake_approval_client.requires_approval.return_value = True
        # Diese Testdatei nutzt (anders als test_api.py) nie `TestClient(app)`,
        # dessen Lifespan `app.state.approval_client` erst setzen würde.
        had_approval_client = hasattr(main.app.state, "approval_client")
        original_approval_client = getattr(main.app.state, "approval_client", None)
        main.app.state.approval_client = fake_approval_client
        try:
            for _ in range(3):
                async with session_factory() as session:
                    document = await repository.get_document(session, document_id)
                    await main._execute_or_defer_forced_deletion(session, document)
        finally:
            if had_approval_client:
                main.app.state.approval_client = original_approval_client
            else:
                del main.app.state.approval_client

        assert fake_approval_client.create_request.await_count == 1

        async with session_factory() as session:
            document = await repository.get_document(session, document_id)
            assert document.force_delete_approval_requested_at is not None
    finally:
        await storage.close()
