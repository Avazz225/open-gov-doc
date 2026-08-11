from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "config-service"

    object_type_service_base_url: str = "http://localhost:8007"
    workflow_service_base_url: str = "http://localhost:8014"
    permission_service_base_url: str = "http://localhost:8004"
    monitoring_service_base_url: str = "http://localhost:8026"
    auth_service_base_url: str = "http://localhost:8003"

    # Gate für POST /config/import (7.3) - dieselbe Domain-Admin-Capability wie
    # workflow-service's Prozessdefinition-Upload (P6-S6-Retrofit): ein voller
    # Konfigurationsimport ist eine Erweiterung derselben "Objekttyp-/Workflow-
    # Konfiguration"-Verantwortung, keine eigene neue Domäne.
    import_required_capability: str = "admin.object_config"

    # Seit P17-S3 (4.3/14.2): reiner NATS-Konsument für
    # `permission.approval.approved`, damit ein per Vier-Augen-Prinzip
    # zurückgestellter `config.import` nach Genehmigung angewendet wird.
    approval_subjects: list[str] = ["permission.approval.approved"]
