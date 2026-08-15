from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "query-service"

    audit_service_base_url: str = "http://localhost:8002"
    document_service_base_url: str = "http://localhost:8006"
    permission_service_base_url: str = "http://localhost:8004"
    auth_service_base_url: str = "http://localhost:8003"
    object_type_service_base_url: str = "http://localhost:8007"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    monitoring_service_base_url: str = "http://localhost:8026"

    query_console_permission: str = "admin.query_console"

    # Manipulationsmodus (6.1, seit P8-S2) - getrennt von der Lese-
    # Berechtigung oben, da Konzept 6.1 explizit "keine Manipulation" als
    # separat vergebbare Einschraenkung nennt.
    query_console_manipulate_permission: str = "admin.query_console.manipulate"

    # HMAC-Schluessel fuer Dry-Run-Tokens (siehe dry_run_tokens.py) - NUR fuer
    # lokale Entwicklung. Eine echte Installation setzt dies ueber ein
    # Secret, gleiches Muster wie archival-service's
    # DMS_ARCHIVE_ENCRYPTION_KEY (ADR 0029).
    dry_run_secret: str = "dev-only-dry-run-secret-do-not-use-in-production"
    dry_run_token_ttl_seconds: int = 300

    # ADR 0031: `pglast` (GPL-3.0-or-later) wird nicht ins Standardimage
    # gebuendelt. Ist diese Einstellung leer, liefert `POST /query` (freier,
    # psql-artiger Abfragetext) 501 - die strukturierte Filter-API
    # (`GET /query/events`) bleibt davon unberuehrt nutzbar. Erwartete
    # Konvention fuer ein echtes Plugin-Modul: es exponiert eine Funktion
    # `get_plugin() -> query_service.parser.ParserPlugin`.
    query_parser_plugin_module: str | None = None
