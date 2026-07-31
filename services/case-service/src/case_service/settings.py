from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "case-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Eine Umlaufmappe (2.3) startet ihren Lebenszyklus über eine Prozessinstanz
    # in workflow-service (P6-S1) und löst dort ihre Dokumentreferenzen live auf
    # (dynamische Referenz auf die jeweils aktuellste Version, siehe repository.py).
    workflow_service_base_url: str = "http://localhost:8014"
    document_service_base_url: str = "http://localhost:8006"
    object_type_service_base_url: str = "http://localhost:8007"

    # Welche Subjects case-service konsumiert (2.3): nur der Abschluss einer
    # Prozessinstanz löst den Abschluss-Snapshot einer Umlaufmappe aus -
    # `workflow.instance.started`/`workflow.task.completed` haben dafür keine
    # Bedeutung. Erster Konsument dieses Events überhaupt - workflow-service war
    # zuvor reiner Producer, siehe docs/services/workflow-service.md "Events".
    subjects: list[str] = ["workflow.instance.completed"]
