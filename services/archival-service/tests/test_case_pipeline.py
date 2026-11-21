import base64
import io
import zipfile

import httpx
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


async def test_run_case_transfers_tick_reaches_failed_permanent_on_verification_mismatch(
    session_factory,
):
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
        max_attempts=1,
    )

    async with session_factory() as session:
        transfer = await repository.get_active_case_transfer_for_case(session, "case-1")
        assert transfer is None
        [failed] = await repository.list_case_transfers(session, status="failed_permanent")
        assert "Verifikation" in failed.error_message


# --- Destruction after retention (P89-S2, ADR 0235) -------------------------


class FakeDestructionCaseClient:
    """case-service's destruction side: `responses` per case id are popped in
    order, `states` is what `find_case` sees afterwards (None = gone)."""

    def __init__(self, due=(), responses=None, states=None):
        self.due = list(due)
        self.responses = responses or {}
        self.states = states or {}
        self.destroy_calls = []

    async def list_due_for_destruction(self):
        return self.due

    async def destroy_case(self, case_id):
        self.destroy_calls.append(case_id)
        result = self.responses[case_id].pop(0)
        if isinstance(result, Exception):
            raise result
        status, body = result
        return httpx.Response(status, json=body)

    async def find_case(self, case_id):
        return self.states.get(case_id)


DESTROYED = (
    200,
    {
        "case_id": "case-1",
        "vorgangsnummer": "2026-0042",
        "mode": "tombstone",
        "destroyed_at": "2026-11-21T10:00:00+00:00",
        "document_reference_count": 3,
    },
)
QUARANTINED = (409, {"detail": "Dokumente in aktiver Quarantäne: doc-1"})
_PAST = "2026-01-01T00:00:00+00:00"
_FUTURE = "2099-01-01T00:00:00+00:00"


def _due_case(**overrides):
    case = {
        "id": "case-1",
        "vorgangsnummer": "2026-0042",
        "archived_at": _PAST,
        "retention_until": _PAST,
        "destroyed_at": None,
    }
    return {**case, **overrides}


async def _tick(session_factory, client, *, max_attempts=5):
    await case_pipeline.run_case_destructions_tick(
        session_factory, case_client=client, max_attempts=max_attempts
    )
    async with session_factory() as session:
        return await repository.list_case_destructions(session)


async def test_a_due_case_is_destroyed_and_recorded(session_factory):
    client = FakeDestructionCaseClient(due=[_due_case()], responses={"case-1": [DESTROYED]})
    [run] = await _tick(session_factory, client)
    assert (run.status, run.mode, run.document_reference_count) == ("destroyed", "tombstone", 3)
    assert run.vorgangsnummer == "2026-0042"
    assert run.destroyed_at is not None

    client.due = []  # case-service drops it from the due list
    [run] = await _tick(session_factory, client)
    assert client.destroy_calls == ["case-1"]  # not destroyed twice


async def test_a_quarantine_blocks_without_using_up_attempts_and_clears(session_factory):
    client = FakeDestructionCaseClient(
        due=[_due_case()],
        responses={"case-1": [QUARANTINED, QUARANTINED, DESTROYED]},
        states={"case-1": _due_case()},
    )
    [run] = await _tick(session_factory, client, max_attempts=1)
    assert (run.status, run.attempts) == ("blocked", 0)
    assert "Quarantäne" in run.error_message
    [run] = await _tick(session_factory, client, max_attempts=1)
    assert run.status == "blocked"  # still quarantined, still no failure
    [run] = await _tick(session_factory, client, max_attempts=1)
    assert (run.status, run.error_message) == ("destroyed", None)


async def test_a_case_no_longer_due_is_cancelled_and_rediscovered_later(session_factory):
    extended = _due_case(retention_until=_FUTURE)
    client = FakeDestructionCaseClient(
        due=[_due_case()],
        responses={"case-1": [(409, {"detail": "Aufbewahrung läuft noch"}), DESTROYED]},
        states={"case-1": extended},
    )
    [run] = await _tick(session_factory, client)
    assert run.status == "cancelled"

    # Due again later: a new run, the cancelled one stays as history.
    runs = await _tick(session_factory, client)
    assert sorted(r.status for r in runs) == ["cancelled", "destroyed"]


async def test_a_destroy_that_went_through_before_a_crash_is_recognized(session_factory):
    """The previous tick's destroy succeeded but the row wasn't updated: the
    next destroy answers 409, and the tombstone/absence tells why."""
    async with session_factory() as session:
        await repository.create_case_destruction(session, "case-1", vorgangsnummer=None)
        await repository.create_case_destruction(session, "case-2", vorgangsnummer=None)
        await session.commit()
    gone = (409, {"detail": "bereits vernichtet"})
    client = FakeDestructionCaseClient(
        responses={"case-1": [gone], "case-2": [gone]},
        states={"case-1": _due_case(destroyed_at="2026-11-20T09:00:00+00:00"), "case-2": None},
    )
    runs = {r.case_id: r for r in await _tick(session_factory, client)}
    assert (runs["case-1"].status, runs["case-1"].mode) == ("destroyed", "tombstone")
    assert (runs["case-2"].status, runs["case-2"].mode) == ("destroyed", "deleted")


async def test_technical_failures_back_off_until_failed_permanent(session_factory):
    client = FakeDestructionCaseClient(
        due=[_due_case()],
        responses={"case-1": [(503, {"detail": "nicht prüfbar"})]},
    )
    [run] = await _tick(session_factory, client, max_attempts=1)
    assert run.status == "failed_permanent"
    assert "503" in run.error_message
    # A permanently failed run is not silently replaced by a new one.
    [run] = await _tick(session_factory, client, max_attempts=1)
    assert client.destroy_calls == ["case-1"]


async def test_an_unreachable_case_service_keeps_the_run_pending_with_backoff(session_factory):
    client = FakeDestructionCaseClient(
        due=[_due_case()], responses={"case-1": [httpx.ConnectError("down")]}
    )
    [run] = await _tick(session_factory, client, max_attempts=3)
    assert (run.status, run.attempts) == ("pending", 1)
    assert run.next_retry_at is not None
