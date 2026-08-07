import base64
import io
import zipfile

import pytest
from archival_service import case_pipeline, repository
from archival_service.keystore import EnvKeyStore


class FakeCaseClient:
    def __init__(self, cases=None, references=None):
        self.cases = cases or {}
        self.references = references or {}
        self.archived_calls = []
        self.due = []

    async def list_due_for_archival(self):
        return self.due

    async def get_case(self, case_id):
        return self.cases[case_id]

    async def list_document_references(self, case_id):
        return self.references.get(case_id, [])

    async def mark_archived(self, case_id):
        self.archived_calls.append(case_id)
        return {}


class FakeDocumentClient:
    def __init__(self, versions=None, contents=None):
        self.versions = versions or {}
        self.contents = contents or {}

    async def get_version(self, document_id, version_number):
        return self.versions[(document_id, version_number)]

    async def download_version_content(self, document_id, version_number):
        return self.contents[(document_id, version_number)]


class FakeStorageClient:
    def __init__(self):
        self.archive_copies = {}
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


def _reference(document_id, version_number, *, removed_at=None):
    return {
        "document_id": document_id,
        "added_by": "u1",
        "added_at": "2026-01-01T00:00:00Z",
        "removed_by": None,
        "removed_at": removed_at,
        "snapshot_version_number": version_number,
        "current_version_number": None,
        "document_deleted_at": None,
    }


async def test_discover_due_case_transfers_creates_pending_transfer(session):
    case_client = FakeCaseClient()
    case_client.due = [{"id": "case-1", "name": "Testfall"}]

    created = await case_pipeline.discover_due_case_transfers(session, case_client)
    await session.commit()

    assert created == 1
    transfer = await repository.get_active_case_transfer_for_case(session, "case-1")
    assert transfer is not None
    assert transfer.status == "pending"


async def test_discover_due_case_transfers_skips_case_with_active_transfer(session):
    await repository.create_case_transfer(session, "case-1")
    await session.commit()
    case_client = FakeCaseClient()
    case_client.due = [{"id": "case-1", "name": "Testfall"}]

    created = await case_pipeline.discover_due_case_transfers(session, case_client)

    assert created == 0


async def test_advance_case_transfer_full_pipeline_reaches_released(session):
    case_client = FakeCaseClient(
        cases={"case-1": {"id": "case-1", "name": "Testvorgang"}},
        references={
            "case-1": [
                _reference("doc-1", 1),
                _reference("doc-2", 2, removed_at="2026-01-02T00:00:00Z"),
            ]
        },
    )
    document_client = FakeDocumentClient(
        versions={("doc-1", 1): {"content_type": "application/pdf", "filename": "Rechnung.pdf"}},
        contents={("doc-1", 1): b"%PDF-fake-content"},
    )
    storage_client = FakeStorageClient()
    keystore = EnvKeyStore(None)

    transfer = await repository.create_case_transfer(session, "case-1")
    await session.commit()

    await case_pipeline.advance_case_transfer(
        session,
        transfer,
        case_client=case_client,
        document_client=document_client,
        storage_client=storage_client,
        keystore=keystore,
        encryption_enabled=False,
    )

    assert transfer.status == "released"
    assert transfer.encrypted is False
    assert transfer.storage_object_key in storage_client.archive_copies
    assert case_client.archived_calls == ["case-1"]

    package = storage_client.archive_copies[transfer.storage_object_key]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        assert "aussonderung.xml" in names
        # Die weich-entfernte Referenz (doc-2) darf NICHT im Paket landen.
        doc_entries = [n for n in names if n.startswith("dokumente/")]
        assert len(doc_entries) == 1


async def test_advance_case_transfer_encrypts_when_requested(session):
    case_client = FakeCaseClient(
        cases={"case-1": {"id": "case-1", "name": "Testvorgang"}},
        references={"case-1": [_reference("doc-1", 1)]},
    )
    document_client = FakeDocumentClient(
        versions={("doc-1", 1): {"content_type": "application/pdf", "filename": "Rechnung.pdf"}},
        contents={("doc-1", 1): b"%PDF-fake-content"},
    )
    storage_client = FakeStorageClient()
    keystore = EnvKeyStore(base64.b64encode(b"k" * 32).decode("ascii"))

    transfer = await repository.create_case_transfer(session, "case-1")
    await session.commit()

    await case_pipeline.advance_case_transfer(
        session,
        transfer,
        case_client=case_client,
        document_client=document_client,
        storage_client=storage_client,
        keystore=keystore,
        encryption_enabled=True,
    )

    assert transfer.encrypted is True
    stored = storage_client.archive_copies[transfer.storage_object_key]
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(stored))


async def test_run_case_transfers_tick_marks_failed_on_verification_mismatch(session_factory):
    async with session_factory() as session:
        await repository.create_case_transfer(session, "case-1")
        await session.commit()

    case_client = FakeCaseClient(
        cases={"case-1": {"id": "case-1", "name": "Testvorgang"}},
        references={"case-1": [_reference("doc-1", 1)]},
    )
    document_client = FakeDocumentClient(
        versions={("doc-1", 1): {"content_type": "application/pdf", "filename": "Rechnung.pdf"}},
        contents={("doc-1", 1): b"%PDF-fake-content"},
    )
    storage_client = FakeStorageClient()
    storage_client.verify_ok = False

    await case_pipeline.run_case_transfers_tick(
        session_factory,
        case_client=case_client,
        document_client=document_client,
        storage_client=storage_client,
        keystore=EnvKeyStore(None),
        encryption_enabled=False,
    )

    async with session_factory() as session:
        transfer = await repository.get_active_case_transfer_for_case(session, "case-1")
        assert transfer is None
        [failed] = await repository.list_case_transfers(session, status="failed")
        assert "Verifikation" in failed.error_message
