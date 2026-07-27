import uuid

import pytest
from virus_scan_service import repository


def _scan_kwargs(**overrides):
    kwargs = {
        "id": str(uuid.uuid4()),
        "document_id": None,
        "filename": "vertrag.pdf",
        "content_type": "application/pdf",
        "size_bytes": 11,
        "checksum_sha256": "abc123",
        "status": "clean",
        "threat_name": None,
        "engine": "eicar",
        "quarantine_object_key": None,
        "created_by": "alice",
    }
    kwargs.update(overrides)
    return kwargs


async def test_create_and_get_scan_result(session):
    created = await repository.create_scan_result(session, **_scan_kwargs())

    fetched = await repository.get_scan_result(session, created.id)

    assert fetched.id == created.id
    assert fetched.status == "clean"


async def test_get_unknown_scan_result_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_scan_result(session, "does-not-exist")


async def test_list_scan_results_filters_by_document_id(session):
    await repository.create_scan_result(session, **_scan_kwargs(document_id="doc-1"))
    await repository.create_scan_result(session, **_scan_kwargs(document_id="doc-2"))

    results = await repository.list_scan_results(session, document_id="doc-1")

    assert len(results) == 1
    assert results[0].document_id == "doc-1"


async def test_infected_scan_result_carries_quarantine_key(session):
    created = await repository.create_scan_result(
        session,
        **_scan_kwargs(
            status="infected",
            threat_name="Eicar-Test-Signature",
            quarantine_object_key="quarantine/abc",
        ),
    )

    assert created.quarantine_object_key == "quarantine/abc"
