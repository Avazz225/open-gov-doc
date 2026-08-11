from datetime import UTC, datetime
from unittest.mock import AsyncMock

from archival_service import browse
from archival_service.models import ArchivalTransfer, CaseArchivalTransfer


def _doc_transfer(**overrides) -> ArchivalTransfer:
    now = datetime.now(UTC)
    fields = {
        "id": "transfer-doc-1",
        "document_id": "doc-1",
        "status": "released",
        "released_at": now,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return ArchivalTransfer(**fields)


def _case_transfer(**overrides) -> CaseArchivalTransfer:
    now = datetime.now(UTC)
    fields = {
        "id": "transfer-case-1",
        "case_id": "case-1",
        "status": "released",
        "released_at": now,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return CaseArchivalTransfer(**fields)


async def test_build_released_items_hydrates_document_and_case():
    document_client = AsyncMock()
    document_client.get_document.return_value = {
        "title": "Rueckmeldung Buergeranfrage",
        "attributes": {"Kennzeichen": "2026-042"},
    }
    case_client = AsyncMock()
    case_client.get_case.return_value = {
        "name": "Bauantrag Musterstrasse",
        "vorgangsnummer": "2026-007",
    }

    items = await browse.build_released_items(
        [_doc_transfer()],
        [_case_transfer()],
        document_client=document_client,
        case_client=case_client,
        dehydration_delay_days=30,
    )

    assert len(items) == 2
    doc_item = next(i for i in items if i["kind"] == "document")
    assert doc_item["title"] == "Rueckmeldung Buergeranfrage"
    assert doc_item["identifier"] == "2026-042"
    assert doc_item["subject_id"] == "doc-1"
    assert doc_item["purge_at"] is not None

    case_item = next(i for i in items if i["kind"] == "case")
    assert case_item["title"] == "Bauantrag Musterstrasse"
    assert case_item["identifier"] == "2026-007"
    assert case_item["purge_at"] is None


async def test_build_released_items_skips_unresolvable_reference():
    document_client = AsyncMock()
    document_client.get_document.side_effect = Exception("not found")
    case_client = AsyncMock()
    case_client.get_case.return_value = {"name": "Case A", "vorgangsnummer": None}

    items = await browse.build_released_items(
        [_doc_transfer()],
        [_case_transfer()],
        document_client=document_client,
        case_client=case_client,
        dehydration_delay_days=30,
    )

    assert len(items) == 1
    assert items[0]["kind"] == "case"


async def test_build_released_items_filters_by_query_against_title_and_identifier():
    document_client = AsyncMock()
    document_client.get_document.return_value = {
        "title": "Rueckmeldung Buergeranfrage",
        "attributes": {"Kennzeichen": "2026-042"},
    }
    case_client = AsyncMock()
    case_client.get_case.return_value = {
        "name": "Bauantrag Musterstrasse",
        "vorgangsnummer": "2026-007",
    }

    matched_by_title = await browse.build_released_items(
        [_doc_transfer()],
        [_case_transfer()],
        document_client=document_client,
        case_client=case_client,
        dehydration_delay_days=30,
        query="buergeranfrage",
    )
    assert [i["kind"] for i in matched_by_title] == ["document"]

    matched_by_identifier = await browse.build_released_items(
        [_doc_transfer()],
        [_case_transfer()],
        document_client=document_client,
        case_client=case_client,
        dehydration_delay_days=30,
        query="2026-007",
    )
    assert [i["kind"] for i in matched_by_identifier] == ["case"]

    no_match = await browse.build_released_items(
        [_doc_transfer()],
        [_case_transfer()],
        document_client=document_client,
        case_client=case_client,
        dehydration_delay_days=30,
        query="does-not-exist",
    )
    assert no_match == []


async def test_build_released_items_sorts_by_released_at_descending():
    document_client = AsyncMock()
    document_client.get_document.return_value = {"title": "Doc", "attributes": {}}
    case_client = AsyncMock()
    case_client.get_case.return_value = {"name": "Case", "vorgangsnummer": None}

    older = _doc_transfer(id="older", released_at=datetime(2025, 1, 1, tzinfo=UTC))
    newer = _case_transfer(id="newer", released_at=datetime(2026, 1, 1, tzinfo=UTC))

    items = await browse.build_released_items(
        [older],
        [newer],
        document_client=document_client,
        case_client=case_client,
        dehydration_delay_days=30,
    )

    assert [i["transfer_id"] for i in items] == ["newer", "older"]
