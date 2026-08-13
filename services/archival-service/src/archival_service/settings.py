from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "archival-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    document_service_base_url: str = "http://localhost:8006"
    rendering_service_base_url: str = "http://localhost:8011"
    storage_service_base_url: str = "http://localhost:8005"
    object_type_service_base_url: str = "http://localhost:8007"
    case_service_base_url: str = "http://localhost:8016"
    # RBAC (Post-Roadmap Phase 19 Session 7, ADR 0072) - archival-service hatte
    # bislang gar keine allgemeine Berechtigungsprüfung (nur `archive_retrieval_
    # role` unten, ein separates, engeres Gate für die Rückholung).
    permission_service_base_url: str = "http://localhost:8004"

    # Poll-Intervall fuer faellige Aussonderungs-/Dehydrierungs-Ticks (5.6) -
    # gleiches Idiom wie document-service's `_retention_poll_loop`/reporting-
    # service's `_report_schedule_poll_loop`.
    archival_poll_interval_seconds: int = 3600

    # Retry/Backoff (Post-Roadmap Phase 20 Session 2, ADR 0078) - gleicher
    # Zahlenwert wie storage-service's bereits bestehendes
    # `max_replication_attempts`, hier fuer beide Transfer-Arten geteilt.
    # Nach Erschoepfung wechselt der Transfer auf `failed_permanent`.
    max_archival_attempts: int = 5

    # Uebergangsfrist (5.6) zwischen erfolgreicher Archivierung
    # ("released") und dem Entfernen der Live-Speicherkopie ("dehydrated") -
    # macht eine versehentliche Aussonderung leicht revidierbar, ohne gleich
    # den vollen Rueckhol-Vorgang zu benoetigen. Gleiches Muster wie
    # `TrashConfig.restore_period_days` in document-service.
    dehydration_delay_days: int = 30

    # Rollen-Gate fuer die Rueckholung (5.6, "Entschluesselung nur fuer
    # berechtigte Rollen") sowie - seit P15-S5 - fuer den lesenden
    # Aussonderungs-Zugriffsbereich (`GET /released-items`, 2.5): Konzept 2.5
    # nennt dafuer "eine dedizierte Archiv-/Registratur-Rolle", die bereits
    # bestehende Rueckhol-Rolle deckt das ab, kein zweites Setting noetig.
    # Gleiches Muster wie `storage_service.governance_bypass_role`/
    # `document_service.kennzeichen_admin_role`.
    archive_retrieval_role: str = "dms-admin"

    # Dev-/Testschluessel fuer `EnvKeyStore` (ADR 0029: nur die
    # `KeyStore`-Schnittstelle wird mitgeliefert, keine echte
    # KDBX-Schluesselverwaltung). 32 Byte, base64-kodiert -
    # AES-256-GCM erwartet exakt diese Laenge.
    archive_encryption_key: str | None = None
