from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "audit-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Welche Subjects der Audit Service konsumiert (Konzept 3.4: "konsumiert alle
    # Events"). Wildcard je Producer-Stream, damit neue Services sich nur hier
    # eintragen müssen, ohne Code-Änderung.
    subjects: list[str] = ["registry.>"]
