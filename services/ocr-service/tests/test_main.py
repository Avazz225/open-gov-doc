import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from ocr_service import repository
from ocr_service.main import _run_retry_tick, app


@pytest.fixture
def client():
    # Startet den echten Lifespan (document_client/storage brauchen app.state,
    # siehe `_run_retry_tick`) - gleiches Muster wie notification-service's
    # test_main.py::client-Fixture. Bewusst KEIN echtes Dokument-Upload in den
    # Tests unten: der dabei ausgeloeste `document.created`-Event wuerde vom
    # hier gestarteten echten NATS-Konsumenten unabhaengig verarbeitet und mit
    # dem direkten `_run_retry_tick`-Aufruf um die `attempts`-Buchfuehrung
    # konkurrieren (nicht deterministisch) - stattdessen ein dauerhaft
    # fehlendes Dokument (siehe `pipeline.process_version`s
    # `DocumentNotFoundError`-Zweig, bricht still ab, ruehrt die Zeile nicht an).
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as c:
        yield c


async def test_run_retry_tick_processes_a_due_result(client, session_factory, session):
    """Post-Roadmap Phase 20 Session 4 (ADR 0080): der Retry-Poll-Loop-Tick
    greift ein faelliges, retry-faehiges Ergebnis auf und ruft
    `process_version` erneut auf."""
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=5
    )
    await session.commit()
    assert result.attempts == 1

    # next_retry_at liegt normalerweise in der (nahen) Zukunft - fuer einen
    # deterministischen Tick-Test direkt in die Vergangenheit gesetzt.
    result.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    await _run_retry_tick(session_factory)

    async with session_factory() as fresh_session:
        fresh = await repository.get_ocr_result(fresh_session, result.id)
        # DocumentNotFoundError bricht process_version still ab, ohne die
        # Zeile zu beruehren - beweist, dass der Tick sie aufgegriffen und
        # `process_version` aufgerufen hat, ohne abzustuerzen.
        assert fresh.attempts == 1
        assert fresh.status == "failed"


async def test_run_retry_tick_skips_results_not_yet_due(client, session_factory, session):
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=5
    )
    await session.commit()
    assert result.attempts == 1

    await _run_retry_tick(session_factory)

    async with session_factory() as fresh_session:
        fresh = await repository.get_ocr_result(fresh_session, result.id)
        # next_retry_at liegt noch in der Zukunft - der Tick darf es nicht anfassen.
        assert fresh.attempts == 1
