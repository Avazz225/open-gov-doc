from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "archival-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    document_service_base_url: str = "http://localhost:8006"
    rendering_service_base_url: str = "http://localhost:8011"
    storage_service_base_url: str = "http://localhost:8005"
    object_type_service_base_url: str = "http://localhost:8007"

    # Poll-Intervall fuer faellige Aussonderungs-/Dehydrierungs-Ticks (5.6) -
    # gleiches Idiom wie document-service's `_retention_poll_loop`/reporting-
    # service's `_report_schedule_poll_loop`.
    archival_poll_interval_seconds: int = 3600

    # Uebergangsfrist (5.6) zwischen erfolgreicher Archivierung
    # ("released") und dem Entfernen der Live-Speicherkopie ("dehydrated") -
    # macht eine versehentliche Aussonderung leicht revidierbar, ohne gleich
    # den vollen Rueckhol-Vorgang zu benoetigen. Gleiches Muster wie
    # `TrashConfig.restore_period_days` in document-service.
    dehydration_delay_days: int = 30

    # Rollen-Gate fuer die Rueckholung (5.6, "Entschluesselung nur fuer
    # berechtigte Rollen") - gleiches Muster wie
    # `storage_service.governance_bypass_role`/
    # `document_service.kennzeichen_admin_role`.
    archive_retrieval_role: str = "dms-admin"

    # Dev-/Testschluessel fuer `EnvKeyStore` (ADR 0029: nur die
    # `KeyStore`-Schnittstelle wird mitgeliefert, keine echte
    # KDBX-Schluesselverwaltung). 32 Byte, base64-kodiert -
    # AES-256-GCM erwartet exakt diese Laenge.
    archive_encryption_key: str | None = None
