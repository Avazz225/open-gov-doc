"""Test-Double fuer die Parser-Plugin-Schnittstelle (ADR 0031/ADR 0029-Muster).

Beweist ausschliesslich die Lade-/Ausfuehrungsmechanik (`query_service.parser.
load_parser_plugin` + `ParserPlugin.parse`) - dies ist KEINE echte SQL-
Grammatik und darf niemals als Vorbild fuer eine echte Implementierung
verwendet werden. Eine reale Implementierung (z. B. auf Basis von `pglast`)
lebt bewusst ausserhalb dieses Repos, siehe query_service/parser.py und
docs/adr/0031-query-konsole-pglast-plugin.md.
"""

import re

from query_service.parser import ParsedQuery, ParserPlugin, ParserPluginError

_QUERY_RE = re.compile(
    r"^SELECT\s+\*\s+FROM\s+(?P<table>\w+)"
    r"(?:\s+WHERE\s+(?P<where>.+?))?"
    r"(?:\s+LIMIT\s+(?P<limit>\d+))?$",
    re.IGNORECASE,
)
_CONDITION_RE = re.compile(r"(\w+)\s*=\s*'([^']*)'")


class FakeParserPlugin(ParserPlugin):
    def parse(self, query_text: str) -> ParsedQuery:
        match = _QUERY_RE.match(query_text.strip())
        if not match:
            raise ParserPluginError(f"Kann Abfragetext nicht parsen: {query_text!r}")
        filters = dict(_CONDITION_RE.findall(match.group("where") or ""))
        limit = int(match.group("limit")) if match.group("limit") else None
        return ParsedQuery(table=match.group("table"), filters=filters, limit=limit)


def get_plugin() -> ParserPlugin:
    return FakeParserPlugin()
