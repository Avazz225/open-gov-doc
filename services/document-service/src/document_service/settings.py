from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "document-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Document Service hält keine Dateiinhalte selbst, sondern spricht dafür
    # ausschließlich die HTTP-API des Storage Service an (3.6) - kein Import
    # von dessen Interna, reine Service-zu-Service-Kommunikation.
    storage_service_base_url: str = "http://localhost:8005"

    # Seit P3-S3: Existenzprüfung des Ordners (2.1) bzw. Constraint-Validierung
    # der Attribute gegen den Objekttyp (2.2/4.5) - beide optional, nur wenn
    # `folder_id`/`object_type_id` beim Anlegen gesetzt werden.
    folder_service_base_url: str = "http://localhost:8008"
    object_type_service_base_url: str = "http://localhost:8007"

    # Verpflichtender Virenscan vor Freigabe eines Uploads (10.3, ADR 0010) -
    # synchron aufgerufen, bevor Inhalt/Metadaten persistiert werden.
    virus_scan_service_base_url: str = "http://localhost:8010"

    # Timeout ohne Aktivität, nach dem eine Bearbeitungssperre automatisch als
    # abgelaufen gilt (4.2) - kein Hintergrund-Sweep nötig, siehe repository.py.
    default_lock_timeout_seconds: float = 1800.0

    # Erste echte Rollenprüfung im gesamten System (P5e-S2): nur Principals,
    # deren vom Gateway injizierter `X-DMS-Roles`-Header diese Rolle enthält,
    # dürfen ein bereits vergebenes Kennzeichen (attributes["Kennzeichen"])
    # nachträglich ändern - für alle anderen ist es rein lesbar.
    kennzeichen_admin_role: str = "dms-admin"

    # Generischer Vier-Augen-Approval-Mechanismus (4.3, P6-S4) - Force-Unlock
    # fragt hier ab, ob Genehmigung nötig ist, und legt bei Bedarf einen
    # Freigabe-Request an, statt sofort auszuführen.
    permission_service_base_url: str = "http://localhost:8004"

    # Welche Subjects document-service konsumiert (P6-S4) - erster Konsument
    # dieses Service überhaupt, bisher reiner Producer. Nur der genehmigte
    # Force-Unlock hat hier Bedeutung, siehe consumer.py.
    subjects: list[str] = ["permission.approval.approved"]
