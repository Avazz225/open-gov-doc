import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from audit_service import deletion_ledger
from audit_service.main import app
from dms_eventbus_client import Event, NatsEventBusClient
from fastapi.testclient import TestClient

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


def make_event(event_type: str, subject: str, **payload) -> Event:
    return Event(
        event_type=event_type,
        service_name="document-service",
        subject=subject,
        payload=payload,
        actor=payload.get("triggered_by"),
    )


def test_append_if_force_deletion_writes_expected_line(tmp_path):
    ledger_path = tmp_path / "deletion-register.jsonl"
    event = make_event(
        "document.force_deleted",
        "doc-1",
        reason="Rechtlich verpflichtend",
        triggered_by="alice",
    )
    deletion_ledger.append_if_force_deletion(event, ledger_path)

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["object_id"] == "doc-1"
    assert entry["object_type"] == "document"
    assert entry["reason"] == "Rechtlich verpflichtend"
    assert entry["triggered_by"] == "alice"


def test_append_if_force_deletion_recognizes_folder_events(tmp_path):
    ledger_path = tmp_path / "deletion-register.jsonl"
    event = make_event("folder.force_deleted", "folder-1", triggered_by="bob")
    deletion_ledger.append_if_force_deletion(event, ledger_path)

    entry = json.loads(ledger_path.read_text(encoding="utf-8").strip())
    assert entry["object_type"] == "folder"


def test_append_if_force_deletion_ignores_unrelated_events(tmp_path):
    ledger_path = tmp_path / "deletion-register.jsonl"
    event = make_event("document.created", "doc-1", title="x")
    deletion_ledger.append_if_force_deletion(event, ledger_path)
    assert not ledger_path.exists()


def test_append_if_force_deletion_ignores_trash_expiry(tmp_path):
    """Nur physische Zwangsloeschung (5.2a) landet im Ledger - regulaere
    Papierkorb-Ablauf-Loeschungen haben laut Konzept 10.4 eine geringere
    Konsequenz und sind bewusst nicht Teil dieser Session, siehe
    docs/operations/backup-restore.md."""
    ledger_path = tmp_path / "deletion-register.jsonl"
    event = Event(
        event_type="document.deleted",
        service_name="document-service",
        subject="doc-1",
        payload={},
    )
    deletion_ledger.append_if_force_deletion(event, ledger_path)
    assert not ledger_path.exists()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _reset_ledger(ledger_path: Path) -> None:
    ledger_path.unlink(missing_ok=True)


def _ledger_has_object_id(ledger_path: Path, object_id: str) -> bool:
    """Reines Sync-Filesystem-I/O, bewusst ausgelagert - ASYNC240 verbietet
    blockierende `pathlib.Path`-Aufrufe direkt im Body einer async-Funktion."""
    if not ledger_path.exists():
        return False
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    return any(json.loads(line)["object_id"] == object_id for line in lines)


async def test_force_deleted_event_appends_real_ledger_line_end_to_end(client):
    """Echter NATS-Roundtrip (wie test_consumer_integration.py) - bestaetigt,
    dass der laufende Consumer (nicht nur die reine Funktion oben) die
    Ledger-Datei tatsaechlich schreibt."""
    ledger_path = Path(os.environ["DMS_DELETION_LEDGER_PATH"])
    _reset_ledger(ledger_path)

    producer = NatsEventBusClient(NATS_URL, stream="document")
    await producer.connect()
    try:
        document_id = f"doc-{uuid.uuid4().hex[:8]}"
        event = Event(
            event_type="document.force_deleted",
            service_name="document-service",
            subject=document_id,
            payload={"reason": "Testfall", "triggered_by": "system:test"},
            occurred_at=datetime.now(UTC),
        )
        await producer.publish(event.event_type, event.to_bytes())

        for _ in range(50):
            if _ledger_has_object_id(ledger_path, document_id):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Ledger-Zeile wurde nicht innerhalb des Timeouts geschrieben")
    finally:
        await producer.close()
