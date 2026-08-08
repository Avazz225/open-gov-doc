from dms_common import BaseServiceSettings

ROOT_FOLDER_ID = "root"


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
