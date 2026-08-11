from dms_common import BaseServiceSettings

ROOT_FOLDER_ID = "root"

# Posteingang/Postausgang (2.5/3.3, P15-S3): weitere feste Sonderordner nach
# demselben Muster wie ROOT_FOLDER_ID (schlichte, fest bekannte ID statt
# UUID, kein eigenes `kind`-Unterscheidungsmerkmal - siehe
# `repository.ensure_special_folders`). Anders als `root` sind diese beiden
# vor Umbenennen/Verschieben/Löschen geschützt (siehe main.py), da Konzept
# §2.5 einen Sonderbereich als "existiert genau einmal je Installation"
# beschreibt - eine bereits vorbestehende, hier bewusst nicht rückwirkend
# geschlossene Lücke bei `root` selbst (siehe "Offene Punkte" in
# docs/services/folder-service.md).
INBOX_FOLDER_ID = "inbox"
OUTBOX_FOLDER_ID = "outbox"
SPECIAL_FOLDER_IDS = frozenset({ROOT_FOLDER_ID, INBOX_FOLDER_ID, OUTBOX_FOLDER_ID})
PROTECTED_FOLDER_IDS = frozenset({INBOX_FOLDER_ID, OUTBOX_FOLDER_ID})


class Settings(BaseServiceSettings):
    service_name: str = "folder-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Objekttyp-Validierung ist optional - nur aufgerufen, wenn ein Folder
    # tatsächlich einen object_type_id trägt (2.2).
    object_type_service_base_url: str = "http://localhost:8007"

    # Aufbewahrung/Legal Hold/Zwangslöschung für Ordner (5.2/5.2a, seit
    # P7-S1b) - Papierkorb/Wiederherstellung kaskadieren synchron auf
    # enthaltene Dokumente (siehe document_client.py), Zwangslöschung fragt
    # darüber ab, ob der Teilbaum noch aktive Dokumente enthält.
    document_service_base_url: str = "http://localhost:8006"

    # Generischer Vier-Augen-Approval-Mechanismus (4.3) - gleiches Muster wie
    # document-service seit P7-S1, hier für den Aktionstyp `folder.force_delete`.
    permission_service_base_url: str = "http://localhost:8004"

    # Welche Subjects folder-service konsumiert (seit P7-S1b) - erster
    # Konsument dieses Service überhaupt, bisher reiner Producer.
    subjects: list[str] = ["permission.approval.approved"]

    # Poll-Intervall des `_retention_poll_loop` (main.py) - gleiches Idiom
    # wie document-service's `retention_poll_interval_seconds` (P7-S1).
    retention_poll_interval_seconds: float = 3600.0

    # Löschabgleich nach Restore (10.4, P11-S4) - gleiche Rolle wie
    # document-service's `kennzeichen_admin_role`, hier für den neuen
    # `POST /folders/{id}/reconcile-restore-deletion`-Endpunkt.
    admin_role: str = "dms-admin"

    # Papierkorb-Familie (2.5, P15-S1): endgültiges Löschen aus dem Papierkorb
    # ist einer eigenen, domänengetrennten Admin-Rolle vorbehalten (4.6) -
    # bewusst eigenständig konfigurierbar statt `admin_role` wiederzuverwenden,
    # gleiches Prinzip wie document-service's separate Rollen-Settings. Keine
    # Verschlusssachen-Variante hier - Konzept 2.5 nennt die Klassifizierung
    # ausdrücklich nur für Dokumente, nicht für Ordner.
    trash_hard_delete_admin_role: str = "dms-admin"
