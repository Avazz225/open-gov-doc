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
    # dieses Service überhaupt, bisher reiner Producer. Force-Unlock UND
    # (seit P7-S1) Zwangslöschung reagieren hier auf genehmigte
    # Freigabe-Requests, siehe consumer.py.
    subjects: list[str] = ["permission.approval.approved"]

    # Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) - Poll-
    # Intervall des `_retention_poll_loop` (main.py), gleiches Idiom wie
    # workflow-service's `sla_poll_interval_seconds` (ADR 0020). Datumsbasierte
    # Fristen brauchen keine minütliche Auflösung, Default entsprechend grob.
    retention_poll_interval_seconds: float = 3600.0

    # Rolle, mit der document-service sich beim Storage Service ausweist,
    # wenn es im Zuge einer sanktionierten Zwangslöschung eine aktive
    # Governance-Mode-Sperre bewusst umgeht (5.1/5.2a) - muss mit
    # `storage-service`s `governance_bypass_role` übereinstimmen (Default
    # auf beiden Seiten `dms-admin`, unabhängig konfigurierbar wie
    # `kennzeichen_admin_role`).
    governance_bypass_role: str = "dms-admin"

    # Lizenz-Limit-Blockade bei Neuanlagen (Konzept 9.3, P9-S2): "verhindert
    # aber neue Anlagen, bis das Limit wieder eingehalten oder die Lizenz
    # erweitert wird" - bestehende Dokumente/Versionen/Wiederherstellung
    # bleiben unberührt (Check nur in `POST /documents`). Fail-open (TTL-
    # Cache) bei nicht erreichbarem license-service, gleiche Konvention wie
    # jeder andere Cross-Service-Cache im Projekt.
    license_service_base_url: str = "http://localhost:8023"
    license_limit_cache_ttl_seconds: float = 30.0

    # Sensor-Konzept (10.1, P11-S1): document-service ist einer der zwei
    # Piloten (kein Vollretrofit, siehe P11-S0-Befund). Aktivierungsstatus
    # kommt vom `monitoring-service`.
    monitoring_service_base_url: str = "http://localhost:8026"
    sensor_sample_interval_seconds: float = 15.0

    # Öffentlicher Freigabelink (4.2a, P14-S10): wer einen Link vorzeitig
    # widerrufen darf, ohne dessen Ersteller zu sein - gleiches unabhängig
    # konfigurierbares Rollen-Setting-Muster wie `kennzeichen_admin_role`/
    # `governance_bypass_role`, auch wenn der Default identisch ist.
    share_link_revoke_admin_role: str = "dms-admin"

    # Papierkorb-Familie (2.5, P15-S1): endgültiges Löschen aus dem Papierkorb
    # ist zwei domänengetrennten Admin-Rollen vorbehalten (4.6, gleiches
    # Setting-Muster wie oben) - eine reguläre und eine engere für als
    # Verschlusssache eingestufte Dokumente (`ObjectType.is_classified`), die
    # nicht zwingend personell identisch besetzt sind.
    trash_hard_delete_admin_role: str = "dms-admin"
    classified_trash_hard_delete_admin_role: str = "dms-admin"

    # Quarantäne-Bereich (2.5/10.3, P15-S2): zweites, unabhängiges Rollen-Gate
    # für `POST /documents/from-quarantine-release` (interner Anlage-Pfad, der
    # bewusst keinen erneuten Virenscan auslöst) - der virus-scan-service
    # prüft dieselbe Freigabe-Berechtigung bereits selbst
    # (`quarantine_admin_role`), unabhängig konfigurierbar wie jedes andere
    # Rollen-Setting-Paar in diesem Projekt.
    quarantine_release_admin_role: str = "dms-admin"
