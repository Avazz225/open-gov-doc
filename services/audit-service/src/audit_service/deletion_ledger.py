import json
from pathlib import Path

from dms_eventbus_client import Event

# Physische Zwangsloeschung (5.2a) - genau die beiden Event-Typen, die
# `document_service`/`folder_service`s `execute_forced_deletion()` publizieren
# (siehe retention_actions.py in beiden Services). Absichtlich NICHT
# "trash_expiry"-Loeschungen (Konzept 10.4: geringere Konsequenz bei
# regulaeren, nicht-physischen Loeschungen - siehe docs/operations/backup-restore.md).
# Since P89-S2 also `case.destroyed` (case-service's destruction after
# retention, ADR 0235): physical for a full deletion, and for a tombstone the
# contents (references, pseudonymized values, name, attributes) are gone -
# either way a restore must not bring the case back.
FORCE_DELETION_EVENT_TYPES = {"document.force_deleted", "folder.force_deleted", "case.destroyed"}


def append_if_force_deletion(event: Event, ledger_path: Path) -> None:
    """Haengt bei einer physischen Zwangsloeschung (10.4/5.2a) eine Zeile an
    ein Append-only-Ledger an - bewusst AUSSERHALB der gemeinsamen Postgres-
    Instanz (eigenes Docker-Volume, siehe infra/docker-compose.yml), damit ein
    DB-Restore (scripts/restore.sh) dieses Register nicht mit zurueckrollt.
    Konzept 10.4 verlangt woertlich, dass es "auf dem aktuellsten Stand
    erhalten bleibt" - nur ein technisch getrennter Kanal erfuellt das."""
    if event.event_type not in FORCE_DELETION_EVENT_TYPES:
        return

    object_type = event.event_type.split(".", 1)[0]
    entry = {
        # event_id ist eine vom Producer erzeugte UUID (dms_eventbus_client.Event),
        # unabhaengig von jeder Postgres-Zeilen-ID - dient als stabile Referenz
        # fuer den Reconcile-Endpunkt-Aufruf (original_entry_id), auch wenn der
        # zugehoerige DeletionRegisterEntry durch einen Restore selbst wieder
        # verschwunden waere.
        "entry_id": event.event_id,
        "object_id": event.subject,
        "object_type": object_type,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "reason": event.payload.get("reason"),
        "triggered_by": event.payload.get("triggered_by") or event.actor,
    }
    if object_type == "case":
        # `restore.sh` checks a tombstone differently from a deleted row, and
        # the Vorgangsnummer is what a reader of the ledger recognizes.
        entry["mode"] = event.payload.get("mode")
        entry["vorgangsnummer"] = event.payload.get("vorgangsnummer")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
