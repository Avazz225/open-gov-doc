from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "query-service"

    audit_service_base_url: str = "http://localhost:8002"
    document_service_base_url: str = "http://localhost:8006"
    permission_service_base_url: str = "http://localhost:8004"
    auth_service_base_url: str = "http://localhost:8003"

    query_console_permission: str = "admin.query_console"

    # ADR 0031: `pglast` (GPL-3.0-or-later) wird nicht ins Standardimage
    # gebuendelt. Ist diese Einstellung leer, liefert `POST /query` (freier,
    # psql-artiger Abfragetext) 501 - die strukturierte Filter-API
    # (`GET /query/events`) bleibt davon unberuehrt nutzbar. Erwartete
    # Konvention fuer ein echtes Plugin-Modul: es exponiert eine Funktion
    # `get_plugin() -> query_service.parser.ParserPlugin`.
    query_parser_plugin_module: str | None = None
