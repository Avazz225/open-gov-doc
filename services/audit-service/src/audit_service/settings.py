from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "audit-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Welche Subjects der Audit Service konsumiert (Konzept 3.4: "konsumiert alle
    # Events"). Wildcard je Producer-Stream, damit neue Services sich nur hier
    # eintragen müssen, ohne Code-Änderung. "document.>" wurde in P3-S2 ergänzt,
    # da 4.2 explizit vollständige Auditierung von Force-Unlock/Konfliktkopie
    # verlangt. "permission.>" kam in P3-S4 dazu, da 4.7 explizit vollständige
    # Auditierung von Bereichssperren (setzen/aufheben) verlangt - andere
    # Services (Auth/Storage) folgen bei Bedarf. "virus_scan.>" kam in P5-S1
    # dazu - 10.3/5.3 verlangen explizit die Auditierung von Scan-Ergebnissen.
    subjects: list[str] = ["registry.>", "document.>", "permission.>", "virus_scan.>"]
