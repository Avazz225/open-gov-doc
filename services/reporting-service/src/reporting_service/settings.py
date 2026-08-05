from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "reporting-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    workflow_service_base_url: str = "http://localhost:8014"
    audit_service_base_url: str = "http://localhost:8002"
    storage_service_base_url: str = "http://localhost:8005"
    notification_service_base_url: str = "http://localhost:8015"
    # Extern erreichbare Gateway-Adresse fuer den Downloadlink in geplanten
    # Berichts-E-Mails (5.4a) - bewusst getrennt von `self_address` (interne
    # Docker-DNS-Adresse fuer die Registry-Selbstregistrierung, fuer einen
    # E-Mail-Empfaenger von aussen nicht erreichbar). Gleiches Prinzip wie
    # `NEXT_PUBLIC_GATEWAY_BASE_URL` in den Frontend-Apps.
    gateway_base_url: str = "http://localhost:8009"

    # Welche Subjects konsumiert werden (nur document.created ist relevant
    # fuer das Dokumentenaufkommen-Read-Modell, siehe consumer.py).
    subjects: list[str] = ["document.>"]

    # Poll-Intervall fuer faellige Berichts-Planungen (5.4a) - gleiches Idiom
    # wie document-service's _retention_poll_loop/workflow-service's
    # _sla_poll_loop. Bewusst niedrig genug fuer Tests/Smoke-Verifikation,
    # per Env in Produktion hochstellbar.
    report_poll_interval_seconds: int = 3600

    # Forensik-Trace Anomalie-Hinweis (5.4b, seit P7-S2c) - einziger
    # Regeltyp laut Konzept ("zunaechst ausschliesslich ueber konfigurierbare
    # statische Schwellwerte"): mehr als `anomaly_download_threshold_count`
    # Downloads durch denselben Akteur innerhalb von
    # `anomaly_download_threshold_minutes` Minuten.
    anomaly_download_threshold_count: int = 20
    anomaly_download_threshold_minutes: int = 5
