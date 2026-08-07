import importlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ParsedQuery:
    """Ergebnis eines geparsten Abfragetexts - bewusst eine kleine,
    strukturierte Zwischendarstellung statt eines ausfuehrbaren AST: der
    eigentliche Zugriff laeuft ueber die foederierten Read-Modell-APIs der
    Owner-Services (Konzept 6.1 "kein Direktzugriff auf fremde DB-Schemata"),
    nicht ueber eine lokal ausgefuehrte SQL-Anweisung."""

    table: str
    filters: dict[str, str] = field(default_factory=dict)
    limit: int | None = None


class ParserPluginError(Exception):
    """Wird geworfen, wenn ein Abfragetext syntaktisch ungueltig ist oder vom
    installierten Plugin nicht unterstuetzt wird."""


class ParserPlugin(ABC):
    """Schnittstelle fuer die optionale, psql-artige Abfragesprache aus
    Konzept 6.1. Referenzimplementierung waere `pglast` (echter PostgreSQL-
    Parser inkl. AST-Visitor-/Printer-Infrastruktur fuer die DMS-spezifischen
    Trace-/Hierarchie-Zusatzkonstrukte) - **nicht** Teil dieses Repos, da
    `pglast` GPL-3.0-or-later lizenziert ist (siehe ADR 0031, analog zur
    KDBX-Plugin-Entscheidung in ADR 0029). Ein Betreiber, der die volle
    Abfragesprache nutzen will, installiert ein eigenes Plugin-Paket, das
    ein Modul mit einer `get_plugin() -> ParserPlugin`-Funktion bereitstellt
    und dieses ueber `DMS_QUERY_PARSER_PLUGIN_MODULE` konfiguriert."""

    @abstractmethod
    def parse(self, query_text: str) -> ParsedQuery:
        """Wirft `ParserPluginError` bei syntaktisch ungueltigem oder nicht
        unterstuetztem Abfragetext."""


def load_parser_plugin(module_path: str | None) -> ParserPlugin | None:
    """Laedt ein Parser-Plugin dynamisch anhand seines Modulpfads. Liefert
    `None`, wenn kein Modul konfiguriert ist - `POST /query` antwortet dann
    mit 501, die strukturierte Filter-API (`GET /query/events`) bleibt davon
    unberuehrt (siehe ADR 0031: kein Alles-oder-Nichts fuer die Konsole)."""
    if not module_path:
        return None
    module = importlib.import_module(module_path)
    plugin = module.get_plugin()
    logger.info("Query-Parser-Plugin geladen: %s", module_path)
    return plugin
