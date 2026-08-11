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
    # "rendering.>" kam in P5-S2 dazu - erzeugte/gescheiterte Ersatzdarstellungen
    # sind ebenfalls Teil der nachvollziehbaren Dokumentverarbeitung. "ocr.>"
    # kam in P5-S3 dazu - 3.9/5.3 verlangen explizit die Auditierung von
    # OCR-Ergebnissen (inkl. needs_review-Fällen). "workflow.>" kam in P6-S1
    # dazu - Prozessinstanz-Start/-Abschluss und Task-Abschlüsse sind Teil
    # der nachvollziehbaren Dokumentverarbeitung. "notification.>" kam in P6-S2
    # dazu - Zustellversuche (erfolgreich/fehlgeschlagen) sind ebenfalls Teil der
    # nachvollziehbaren Systemaktionen (5.3). "case.>" kam in P6-S3 dazu -
    # Anlage/Referenzänderung/Abschluss einer Umlaufmappe (2.3) sind ebenfalls
    # Teil der nachvollziehbaren Vorgangsbearbeitung. "auth.>" kam in P6-S5
    # dazu - Superuser-Break-Glass-Aktivierung/-Deaktivierung (4.6) verlangt
    # explizit erhöhte Auditierungspriorität für den Lebenszyklus selbst
    # (siehe ADR 0023 für die bewusst NICHT umgesetzte Priorisierung
    # einzelner *während* der Aktivierung durchgeführter Fremdaktionen).
    # "signature.>" kam in P6-S7 dazu - elektronische Signaturen (3.10) sind
    # kryptografisch an eine konkrete Dokumentversion gebunden und damit
    # ebenfalls Teil der nachvollziehbaren Dokumentverarbeitung. "favorite.>"
    # kam in P7-S1d dazu - Favoriten-Änderungen sollen wie jede andere
    # Nutzeraktion im Audit-Trail nachvollziehbar bleiben. "folder.>" fehlte
    # bislang komplett (echter, beim P7-S2-Live-Smoke-Test entdeckter
    # Bestandsfehler - folder-service hat seit P7-S1b einen eigenen
    # Event-Stream, wurde aber nie in diese Liste aufgenommen) - ohne dieses
    # Subject wäre die Forensik-Trace (5.4b) für sämtliche Ordner-Aktionen
    # blind, direkt im Widerspruch zum Zweck dieser Session. "query.>" kam
    # in P8-S1 dazu - die Query- & Trace-Konsole (6.1) auditiert jede
    # ausgeführte Abfrage selbst (`query.executed`, Konzept-Punkt 5
    # "Vollständige Protokollierung"), proaktiv gleich mit ergänzt statt den
    # "folder.>"-Fehler ein zweites Mal zu wiederholen. "license.>" kam in
    # P9-S1 dazu - `license-service`s Installations-/Statusänderungs-
    # Ereignisse (9.2) sollen ebenfalls im Audit-Trail landen.
    subjects: list[str] = [
        "registry.>",
        "document.>",
        "permission.>",
        "virus_scan.>",
        "rendering.>",
        "ocr.>",
        "workflow.>",
        "notification.>",
        "case.>",
        "auth.>",
        "signature.>",
        "favorite.>",
        "folder.>",
        "reporting.>",
        "query.>",
        "license.>",
        # "orchestration.>" kam in P10-S1 dazu - der Plugin Orchestration
        # Service (3.8) auditiert seine eigenen Platzierungsentscheidungen
        # ("orchestration.placement.decided") ueber denselben Event-Trail.
        "orchestration.>",
        # "monitoring.>" kam in P11-S1 dazu - der monitoring-service auditiert
        # jede Sensor-Konfigurationsaenderung ("monitoring.sensor.config_changed",
        # 10.1: Deaktivieren sicherheitsrelevanter Sensoren ist selbst ein
        # sicherheitsrelevanter Vorgang).
        "monitoring.>",
        # "teamspace.>" kam in P14-S6 dazu - der neue teamspace-service (2.5)
        # auditiert Anlage/Loeschung eines Team-Arbeitsbereichs sowie
        # Einladen/Entfernen von Mitgliedern (sicherheitsrelevant, da dies das
        # eigentliche, von der uebrigen RBAC unabhaengige Zugriffsregime ist).
        "teamspace.>",
        # "mail_connector.>" kam in P15-S3 dazu - der neue mail-connector
        # (2.5/3.3) auditiert Empfang/Zuordnung/Versand externer Korrespondenz
        # durch die Poststelle-Rolle.
        "mail_connector.>",
    ]

    # Loeschregister-Ledger (10.4, P11-S4): Append-only-Datei auf einem
    # eigenen Docker-Volume (deletion-ledger-data), bewusst AUSSERHALB der
    # gemeinsamen Postgres-Instanz - ein DB-Restore (scripts/restore.sh)
    # rollt dieses Register damit nicht mit zurueck, siehe
    # docs/operations/backup-restore.md.
    deletion_ledger_path: str = "/deletion-ledger/deletion-register.jsonl"
