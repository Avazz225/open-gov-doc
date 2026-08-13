import re
from dataclasses import dataclass

from mail_connector.case_client import CaseClient
from mail_connector.document_client import DocumentClient

# Generischer Rückfall-Kandidaten-Muster (2.5/3.3, P15-S3): erkennt Token der
# Form "<alnum>-<alnum>"/"<alnum>/<alnum>" (2-10 Zeichen je Seite), z. B.
# "2026-001" - deckt die BEIDEN Default-Formate `{YYYY}-{Laufende_Nummer}` ab.
# Seit Post-Roadmap Phase 19 Session 11 nur noch der Fallback, falls sich
# `build_candidate_pattern()` unten (aus den tatsächlich konfigurierten
# Formaten abgeleitet, je Nachricht neu geladen) nicht befüllen ließ (z. B.
# object-type-service/case-service kurzzeitig nicht erreichbar) - siehe
# main.py's `_load_candidate_pattern`.
_FALLBACK_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9]{2,10}[-/][A-Za-z0-9]{2,10}\b")

# Platzhalter->Regex-Fragment, spiegelbildlich zu object-type-service's
# `_render_kennzeichen`/case-service's `_render_vorgangsnummer` (beide bauen
# nur format->Wert per `str.format()`, nie die Rückrichtung - dies ist die
# erste Umkehrung im Projekt). `Laufende_Nummer` wird zwar mit `:03d}`
# zeilenweise auf mindestens 3 Stellen gepolstert, aber nirgends als feste
# Breite validiert - `\d+` statt `\d{3}`, um eine vierstellige laufende
# Nummer nicht abzuschneiden. Jeder andere Platzhalter (nur object-type-
# service, attributbasiert seit P17-S2, z. B. `{Federführung}`) ist freier
# Text ohne bekanntes Format - generisches, nicht-gieriges Nicht-Leerraum-
# Muster.
_PLACEHOLDER_PATTERNS = {
    "YYYY": r"\d{4}",
    "YY": r"\d{2}",
    "MM": r"\d{2}",
    "DD": r"\d{2}",
    "Laufende_Nummer": r"\d+",
}
_ATTRIBUTE_PLACEHOLDER_PATTERN = r"\S+?"
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _format_to_regex_fragment(format_str: str) -> str:
    """Übersetzt einen `{Platzhalter}`-Formatstring (Kennzeichen/
    Vorgangsnummer) in ein Regex-Fragment - literale Zeichen zwischen
    Platzhaltern werden escaped, Platzhalter durch ihr Muster ersetzt."""
    pattern_parts: list[str] = []
    last_end = 0
    for match in _PLACEHOLDER_RE.finditer(format_str):
        pattern_parts.append(re.escape(format_str[last_end : match.start()]))
        placeholder = match.group(1)
        pattern_parts.append(_PLACEHOLDER_PATTERNS.get(placeholder, _ATTRIBUTE_PLACEHOLDER_PATTERN))
        last_end = match.end()
    pattern_parts.append(re.escape(format_str[last_end:]))
    return "".join(pattern_parts)


def build_candidate_pattern(formats: list[str]) -> re.Pattern[str]:
    """Baut das Kandidaten-Muster aus den tatsächlich konfigurierten
    `kennzeichen_format`/`case_number_config.format`-Werten (Post-Roadmap
    Phase 19 Session 11) - Rückfall auf das generische Muster, falls keine
    Formate übergeben wurden (z. B. ein Cross-Service-Aufruf ist
    fehlgeschlagen, siehe main.py's `_load_candidate_pattern`).

    Sortierung nach absteigender Formatlänge (nicht alphabetisch): Pythons
    `re`-Alternation ist "erste passende Alternative gewinnt", kein
    längster-Treffer-Matching wie POSIX. Bei zwei Formaten, von denen eines
    ein Präfix-kompatibles Teilmuster des anderen ist (z. B. `{Federführung}-
    {Laufende_Nummer}` vs. `{Federführung}-{YYYY}-{Laufende_Nummer}`), würde
    eine alphabetische Sortierung das KÜRZERE Muster zuerst versuchen und
    einen Kandidaten wie `Recht-2026-004` fälschlich schon nach `Recht-2026`
    abschneiden (live in Post-Roadmap Phase 19 Session 11 gefunden, als eine
    dritte, kürzere Format-Variante zu den bis dahin getesteten zwei
    hinzukam). Längste Formate zuerst probiert schließt das aus."""
    unique_formats = sorted(set(formats), key=lambda f: (-len(f), f))
    if not unique_formats:
        return _FALLBACK_CANDIDATE_RE
    fragments = [_format_to_regex_fragment(f) for f in unique_formats]
    return re.compile(rf"\b(?:{'|'.join(fragments)})\b")


@dataclass
class MatchResult:
    candidates: list[str]
    match_type: str | None  # "kennzeichen" | "vorgangsnummer"
    match_value: str | None
    target_type: str | None  # "document" | "case"
    target_id: str | None


def extract_candidates(
    text: str, *, pattern: re.Pattern[str] = _FALLBACK_CANDIDATE_RE
) -> list[str]:
    # Reihenfolge- und duplikatfrei (dict.fromkeys statt set) - deterministische
    # Ergebnisse für Tests/Nachvollziehbarkeit.
    return list(dict.fromkeys(pattern.findall(text)))


async def resolve_match(
    text: str,
    *,
    document_client: DocumentClient,
    case_client: CaseClient,
    pattern: re.Pattern[str] = _FALLBACK_CANDIDATE_RE,
) -> MatchResult:
    """Sucht in Betreff+Text nach einem eindeutigen Kennzeichen-/
    Vorgangsnummer-Treffer (2.5/10.3) - mehrdeutige oder fehlende Treffer
    bleiben `unassigned`, die Poststelle sieht in jedem Fall alle
    Kandidaten-Token für die manuelle Zuordnung."""
    candidates = extract_candidates(text, pattern=pattern)
    hits: list[tuple[str, str, str, str]] = []  # (match_type, match_value, target_type, target_id)
    for candidate in candidates:
        for document in await document_client.lookup_by_kennzeichen(candidate):
            hits.append(("kennzeichen", candidate, "document", document["id"]))
        for case in await case_client.lookup_by_vorgangsnummer(candidate):
            hits.append(("vorgangsnummer", candidate, "case", case["id"]))

    if len(hits) == 1:
        match_type, match_value, target_type, target_id = hits[0]
        return MatchResult(
            candidates=candidates,
            match_type=match_type,
            match_value=match_value,
            target_type=target_type,
            target_id=target_id,
        )
    return MatchResult(
        candidates=candidates, match_type=None, match_value=None, target_type=None, target_id=None
    )
