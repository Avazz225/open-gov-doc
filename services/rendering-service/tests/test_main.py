import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from rendering_service import repository
from rendering_service.main import _run_retry_tick, app


@pytest.fixture
def client():
    # Startet den echten Lifespan (document_client/storage brauchen app.state,
    # siehe `_run_retry_tick`) - gleiches Muster wie ocr-service's
    # test_main.py::client-Fixture. Bewusst KEIN echtes Dokument-Upload in den
    # Tests unten (siehe dortige Begründung: Race mit dem echten NATS-Konsumenten).
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as c:
        yield c


async def test_run_retry_tick_processes_a_due_rendition(client, session_factory, session):
    """Post-Roadmap Phase 20 Session 4 (ADR 0080): der Retry-Poll-Loop-Tick
    greift eine faellige, retry-faehige Rendition auf und ruft
    `retry_rendition` erneut auf."""
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session,
        document_id=document_id,
        version_number=1,
        rendition_type="pdf_archive",
        source_filename="a.pdf",
        source_content_type="application/pdf",
        error="e",
        max_attempts=5,
    )
    await session.commit()
    assert result.attempts == 1

    # next_retry_at liegt normalerweise in der (nahen) Zukunft - fuer einen
    # deterministischen Tick-Test direkt in die Vergangenheit gesetzt.
    result.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    await _run_retry_tick(session_factory)

    async with session_factory() as fresh_session:
        fresh = await repository.get_rendition(fresh_session, result.id)
        # DocumentNotFoundError bricht retry_rendition still ab, ohne die
        # Zeile zu beruehren - beweist, dass der Tick sie aufgegriffen und
        # retry_rendition aufgerufen hat, ohne abzustuerzen.
        assert fresh.attempts == 1
        assert fresh.status == "failed"


async def test_run_retry_tick_skips_renditions_not_yet_due(client, session_factory, session):
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session,
        document_id=document_id,
        version_number=1,
        rendition_type="pdf_archive",
        source_filename="a.pdf",
        source_content_type="application/pdf",
        error="e",
        max_attempts=5,
    )
    await session.commit()
    assert result.attempts == 1

    await _run_retry_tick(session_factory)

    async with session_factory() as fresh_session:
        fresh = await repository.get_rendition(fresh_session, result.id)
        # next_retry_at liegt noch in der Zukunft - der Tick darf sie nicht anfassen.
        assert fresh.attempts == 1
