import base64
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from archival_service import pipeline, repository
from archival_service.keystore import EnvKeyStore


class FakeDocumentClient:
    def __init__(self, documents=None, versions=None, holds=None):
        self.documents = documents or {}
        self.versions = versions or {}
        self.holds = holds or {}
        self.archived_calls = []
        self.dehydrated_calls = []
        self.due = []

    async def list_due_for_archival(self):
        return self.due

    async def get_document(self, document_id):
        return self.documents[document_id]

    async def get_version(self, document_id, version_number):
        return self.versions[(document_id, version_number)]

    async def has_active_hold(self, document_id):
        return self.holds.get(document_id, False)

    async def mark_archived(self, document_id, *, archive_format):
        self.archived_calls.append((document_id, archive_format))
        return {}

    async def mark_dehydrated(self, document_id):
        self.dehydrated_calls.append(document_id)
        return {}

    async def mark_rehydrated(self, document_id):
        return {}


class FakeRenderingClient:
    def __init__(self, renditions=None, content=b"%PDF-fake-content"):
        self.renditions = renditions or {}
        self.content = content

    async def get_pdf_archive_rendition(self, *, document_id, version_number):
        return self.renditions.get((document_id, version_number))

    async def download_rendition_content(self, rendition_id):
        return self.content


class FakeObjectTypeClient:
    def __init__(self, object_types=None):
        self.object_types = object_types or {}

    async def get_object_type(self, object_type_id):
        return self.object_types[object_type_id]


class FakeStorageClient:
    def __init__(self):
        self.archive_copies = {}
        self.live_uploads = {}
        self.deleted_live_keys = []
        self.verify_ok = True

    async def upload_archive_copy(self, key, data, content_type):
        self.archive_copies[key] = data

    async def verify_archive_copy(self, key):
        return [
            {
                "backend_id": "archive",
                "status": "ok" if self.verify_ok else "mismatch",
                "ok": self.verify_ok,
            }
        ]

    async def download_archive_copy(self, key):
        return self.archive_copies[key]

    async def upload(self, key, data, content_type):
        self.live_uploads[key] = data

    async def delete_live_copies(self, key):
        self.deleted_live_keys.append(key)


def _ready_rendition(rendition_id="r1", *, status="ready"):
    return {
        "id": rendition_id,
        "rendition_type": "pdf_archive",
        "status": status,
        "target_content_type": "application/pdf",
        "error_message": None,
    }


async def test_discover_due_transfers_creates_pending_transfer(session):
    doc_client = FakeDocumentClient()
    doc_client.due = [{"id": "doc-1"}]

    created = await pipeline.discover_due_transfers(session, doc_client)
    await session.commit()

    assert created == 1
    transfer = await repository.get_active_transfer_for_document(session, "doc-1")
    assert transfer is not None
    assert transfer.status == "pending"


async def test_discover_due_transfers_skips_document_with_active_transfer(session):
    await repository.create_transfer(session, "doc-1")
    await session.commit()
    doc_client = FakeDocumentClient()
    doc_client.due = [{"id": "doc-1"}]

    created = await pipeline.discover_due_transfers(session, doc_client)

    assert created == 0


async def test_advance_transfer_full_pipeline_reaches_released(session):
    doc_client = FakeDocumentClient(
        documents={"doc-1": {"id": "doc-1", "current_version_number": 1, "object_type_id": None}},
    )
    rendering_client = FakeRenderingClient(renditions={("doc-1", 1): _ready_rendition()})
    object_type_client = FakeObjectTypeClient()
    storage_client = FakeStorageClient()
    keystore = EnvKeyStore(None)

    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    await pipeline.advance_transfer(
        session,
        transfer,
        document_client=doc_client,
        rendering_client=rendering_client,
        object_type_client=object_type_client,
        storage_client=storage_client,
        keystore=keystore,
    )

    assert transfer.status == "released"
    assert transfer.archive_format == "pdf_a"
    assert transfer.encrypted is False
    assert transfer.storage_object_key in storage_client.archive_copies
    assert transfer.checksum_sha256 == hashlib.sha256(rendering_client.content).hexdigest()
    assert doc_client.archived_calls == [("doc-1", "pdf_a")]


async def test_advance_transfer_stays_locked_when_rendition_not_ready(session):
    doc_client = FakeDocumentClient(
        documents={"doc-1": {"id": "doc-1", "current_version_number": 1, "object_type_id": None}},
    )
    rendering_client = FakeRenderingClient(renditions={})
    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    await pipeline.advance_transfer(
        session,
        transfer,
        document_client=doc_client,
        rendering_client=rendering_client,
        object_type_client=FakeObjectTypeClient(),
        storage_client=FakeStorageClient(),
        keystore=EnvKeyStore(None),
    )

    assert transfer.status == "locked"


async def test_advance_transfer_raises_when_rendition_failed(session):
    doc_client = FakeDocumentClient(
        documents={"doc-1": {"id": "doc-1", "current_version_number": 1, "object_type_id": None}},
    )
    rendering_client = FakeRenderingClient(
        renditions={("doc-1", 1): _ready_rendition(status="failed")}
    )
    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    with pytest.raises(pipeline.PipelineError):
        await pipeline.advance_transfer(
            session,
            transfer,
            document_client=doc_client,
            rendering_client=rendering_client,
            object_type_client=FakeObjectTypeClient(),
            storage_client=FakeStorageClient(),
            keystore=EnvKeyStore(None),
        )


async def test_advance_transfer_encrypts_when_object_type_requires_it(session):
    doc_client = FakeDocumentClient(
        documents={"doc-1": {"id": "doc-1", "current_version_number": 1, "object_type_id": 42}},
    )
    rendering_client = FakeRenderingClient(renditions={("doc-1", 1): _ready_rendition()})
    object_type_client = FakeObjectTypeClient(
        object_types={42: {"archive_encryption_enabled": True}}
    )
    storage_client = FakeStorageClient()
    keystore = EnvKeyStore(base64.b64encode(b"k" * 32).decode("ascii"))

    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    await pipeline.advance_transfer(
        session,
        transfer,
        document_client=doc_client,
        rendering_client=rendering_client,
        object_type_client=object_type_client,
        storage_client=storage_client,
        keystore=keystore,
    )

    assert transfer.encrypted is True
    stored = storage_client.archive_copies[transfer.storage_object_key]
    assert stored != rendering_client.content


async def test_run_active_transfers_tick_marks_failed_on_verification_mismatch(session_factory):
    async with session_factory() as session:
        await repository.create_transfer(session, "doc-1")
        await session.commit()

    doc_client = FakeDocumentClient(
        documents={"doc-1": {"id": "doc-1", "current_version_number": 1, "object_type_id": None}},
    )
    rendering_client = FakeRenderingClient(renditions={("doc-1", 1): _ready_rendition()})
    storage_client = FakeStorageClient()
    storage_client.verify_ok = False

    await pipeline.run_active_transfers_tick(
        session_factory,
        document_client=doc_client,
        rendering_client=rendering_client,
        object_type_client=FakeObjectTypeClient(),
        storage_client=storage_client,
        keystore=EnvKeyStore(None),
    )

    async with session_factory() as session:
        transfer = await repository.get_active_transfer_for_document(session, "doc-1")
        assert transfer is None
        [failed] = await repository.list_transfers(session, status="failed")
        assert "Verifikation" in failed.error_message


async def test_run_dehydration_tick_skips_document_with_active_hold(session_factory):
    async with session_factory() as session:
        transfer = await repository.create_transfer(session, "doc-1")
        await repository.update_status(
            session, transfer, status="released", released_at=datetime.now(UTC) - timedelta(days=31)
        )
        await session.commit()

    doc_client = FakeDocumentClient(holds={"doc-1": True})
    storage_client = FakeStorageClient()

    await pipeline.run_dehydration_tick(
        session_factory, document_client=doc_client, storage_client=storage_client, delay_days=30
    )

    assert storage_client.deleted_live_keys == []
    async with session_factory() as session:
        transfer = await repository.get_transfer(session, transfer.id)
        assert transfer.status == "released"


async def test_run_dehydration_tick_removes_live_copy_and_marks_dehydrated(session_factory):
    async with session_factory() as session:
        transfer = await repository.create_transfer(session, "doc-1")
        await repository.update_status(
            session, transfer, status="released", released_at=datetime.now(UTC) - timedelta(days=31)
        )
        await session.commit()
        transfer_id = transfer.id

    doc_client = FakeDocumentClient(
        documents={"doc-1": {"id": "doc-1", "current_version_number": 1}},
        versions={("doc-1", 1): {"storage_object_key": "documents/doc-1/abc"}},
        holds={"doc-1": False},
    )
    storage_client = FakeStorageClient()

    await pipeline.run_dehydration_tick(
        session_factory, document_client=doc_client, storage_client=storage_client, delay_days=30
    )

    assert storage_client.deleted_live_keys == ["documents/doc-1/abc"]
    assert doc_client.dehydrated_calls == ["doc-1"]
    async with session_factory() as session:
        transfer = await repository.get_transfer(session, transfer_id)
        assert transfer.status == "dehydrated"
        assert transfer.dehydrated_at is not None
