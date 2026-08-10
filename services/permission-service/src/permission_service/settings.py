from dms_common import BaseServiceSettings

ROOT_RESOURCE_ID = "root"


class Settings(BaseServiceSettings):
    service_name: str = "permission-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Provisorischer Vertrag (siehe docs/services/permission-service.md): welche
    # Subjects Struktur-Events (Ressource angelegt/verschoben/gelöscht) liefern.
    # Erwarteter Producer: Folder Service (P3-S3, noch nicht gebaut) unter
    # Stream "folder" - bis dahin per Tests simuliert.
    structure_subjects: list[str] = ["folder.>"]

    # Selbst-Konsum des eigenen Vier-Augen-Approval-Events (4.3, P6-S4) für
    # Aktionstypen, die permission-service selbst ausführt (Bereichssperren,
    # seit P6-S6 zusätzlich die Not-Shutdown-Auslösung) - siehe approval_consumer.py.
    approval_subjects: list[str] = ["permission.approval.approved"]

    # Erster Cross-Service-Aufruf dieses Service (P6-S6, 4.8): nur der aktive
    # Superuser darf den Wartungsmodus aufheben, dessen Identität lebt in auth-service.
    auth_service_base_url: str = "http://localhost:8003"

    # Stellvertretung (4.4a, P14-S11): wer eine Delegation vorzeitig widerrufen
    # darf, ohne die vertretene Person selbst zu sein - gleiches unabhängig
    # konfigurierbares Rollen-Setting-Muster wie document-services
    # `share_link_revoke_admin_role` (P14-S10), auch wenn der Default identisch ist.
    delegation_revoke_admin_role: str = "dms-admin"
