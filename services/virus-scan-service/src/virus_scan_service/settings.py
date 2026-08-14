from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "virus-scan-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # The virus scan service does not hold infected files itself, but stores
    # them like any other content via the Storage Service (3.6) - consistent
    # with the Document Service, which also never holds bytes itself.
    storage_service_base_url: str = "http://localhost:8005"

    # Swappable engine following the same plugin principle as the storage
    # backends (3.3/3.8, ADR 0010): "eicar" (default, recognizes only the
    # standardized EICAR test signature, no real malware protection) or
    # "clamd" (real engine against a separately operated clamd daemon).
    scan_engine: str = "eicar"
    clamd_host: str = "clamav"
    clamd_port: int = 3310
    clamd_timeout_seconds: float = 15.0

    # Quarantine area (2.5/10.3, P15-S2): releasing a false positive calls
    # the Document Service's internal creation path (no re-scan, see
    # document-service main.py "from-quarantine-release").
    document_service_base_url: str = "http://localhost:8006"

    # RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - replaces the
    # previous plain `X-DMS-Roles` gate (`quarantine_admin_role`) with a
    # real permission-service check (`admin.quarantine`, role
    # "domain-admin-virus-scan").
    permission_service_base_url: str = "http://localhost:8004"
