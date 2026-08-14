from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "config-service"

    object_type_service_base_url: str = "http://localhost:8007"
    workflow_service_base_url: str = "http://localhost:8014"
    permission_service_base_url: str = "http://localhost:8004"
    monitoring_service_base_url: str = "http://localhost:8026"
    auth_service_base_url: str = "http://localhost:8003"

    # Gate for POST /config/import (7.3) - the same domain admin capability as
    # workflow-service's process definition upload (P6-S6 retrofit): a full
    # configuration import is an extension of the same "object type/workflow
    # configuration" responsibility, not a new domain of its own.
    import_required_capability: str = "admin.object_config"

    # Since P17-S3 (4.3/14.2): pure NATS consumer for
    # `permission.approval.approved`, so that a `config.import` deferred via
    # the four-eyes principle is applied after approval.
    approval_subjects: list[str] = ["permission.approval.approved"]
