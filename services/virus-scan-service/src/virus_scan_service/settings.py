from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "virus-scan-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Virus-Scan Service hält infizierte Dateien nicht selbst, sondern legt sie
    # wie jeden anderen Inhalt über den Storage Service ab (3.6) - konsistent
    # mit dem Document Service, der auch nie selbst Bytes hält.
    storage_service_base_url: str = "http://localhost:8005"

    # Austauschbare Engine nach demselben Plugin-Prinzip wie die
    # Storage-Backends (3.3/3.8, ADR 0010): "eicar" (Standard, erkennt nur die
    # standardisierte EICAR-Testsignatur, kein echter Malware-Schutz) oder
    # "clamd" (echte Engine gegen einen separat betriebenen clamd-Daemon).
    scan_engine: str = "eicar"
    clamd_host: str = "clamav"
    clamd_port: int = 3310
    clamd_timeout_seconds: float = 15.0

    # Quarantäne-Bereich (2.5/10.3, P15-S2): Freigabe eines Fehlalarms ruft
    # den internen Anlage-Pfad des Document Service auf (kein erneuter Scan,
    # siehe document-service main.py "from-quarantine-release").
    document_service_base_url: str = "http://localhost:8006"

    # RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - ersetzt das
    # bisherige reine `X-DMS-Roles`-Gate (`quarantine_admin_role`) durch eine
    # echte permission-service-Prüfung (`admin.quarantine`, Rolle
    # "domain-admin-virus-scan").
    permission_service_base_url: str = "http://localhost:8004"
