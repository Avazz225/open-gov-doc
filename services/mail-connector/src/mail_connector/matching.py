import re
from dataclasses import dataclass

from mail_connector.case_client import CaseClient
from mail_connector.document_client import DocumentClient

# Generic fallback candidate pattern (2.5/3.3, P15-S3): recognizes tokens of
# the form "<alnum>-<alnum>"/"<alnum>/<alnum>" (2-10 characters per side),
# e.g. "2026-001" - covers BOTH default formats `{YYYY}-{Laufende_Nummer}`.
# Since Post-Roadmap Phase 19 Session 11 this is only the fallback, in case
# `build_candidate_pattern()` below (derived from the actually configured
# formats, reloaded per message) could not be populated (e.g.
# object-type-service/case-service briefly unreachable) - see main.py's
# `_load_candidate_pattern`.
_FALLBACK_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9]{2,10}[-/][A-Za-z0-9]{2,10}\b")

# Placeholder->regex fragment, mirroring object-type-service's
# `_render_kennzeichen`/case-service's `_render_vorgangsnummer` (both only
# build format->value via `str.format()`, never the reverse direction - this
# is the first reversal in the project). `Laufende_Nummer` (running number)
# is padded to at least 3 digits with `:03d}` when rendered, but nowhere
# validated as a fixed width - `\d+` instead of `\d{3}`, so as not to
# truncate a four-digit running number. Every other placeholder (only
# object-type-service, attribute-based since P17-S2, e.g. `{Federführung}`)
# is free text with no known format - generic, non-greedy non-whitespace
# pattern.
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
    """Translates a `{placeholder}` format string (reference number/case
    number) into a regex fragment - literal characters between placeholders
    are escaped, placeholders are replaced by their pattern."""
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
    """Builds the candidate pattern from the actually configured
    `kennzeichen_format`/`case_number_config.format` values (Post-Roadmap
    Phase 19 Session 11) - falls back to the generic pattern if no formats
    were passed (e.g. a cross-service call failed, see main.py's
    `_load_candidate_pattern`).

    Sorted by descending format length (not alphabetically): Python's `re`
    alternation is "first matching alternative wins", not longest-match
    matching like POSIX. With two formats where one is a prefix-compatible
    sub-pattern of the other (e.g. `{Federführung}-{Laufende_Nummer}` vs.
    `{Federführung}-{YYYY}-{Laufende_Nummer}`), an alphabetical sort would
    try the SHORTER pattern first and incorrectly truncate a candidate like
    `Recht-2026-004` already after `Recht-2026` (found live in Post-Roadmap
    Phase 19 Session 11, when a third, shorter format variant was added to
    the two tested until then). Trying the longest formats first rules this
    out."""
    unique_formats = sorted(set(formats), key=lambda f: (-len(f), f))
    if not unique_formats:
        return _FALLBACK_CANDIDATE_RE
    fragments = [_format_to_regex_fragment(f) for f in unique_formats]
    return re.compile(rf"\b(?:{'|'.join(fragments)})\b")


@dataclass
class MatchResult:
    candidates: list[str]
    match_type: str | None  # "kennzeichen" (reference number) | "vorgangsnummer" (case number)
    match_value: str | None
    target_type: str | None  # "document" | "case"
    target_id: str | None


def extract_candidates(
    text: str, *, pattern: re.Pattern[str] = _FALLBACK_CANDIDATE_RE
) -> list[str]:
    # Order-preserving and duplicate-free (dict.fromkeys instead of set) -
    # deterministic results for tests/traceability.
    return list(dict.fromkeys(pattern.findall(text)))


async def resolve_match(
    text: str,
    *,
    document_client: DocumentClient,
    case_client: CaseClient,
    pattern: re.Pattern[str] = _FALLBACK_CANDIDATE_RE,
) -> MatchResult:
    """Searches subject+body for a unique reference number/case number match
    (2.5/10.3) - ambiguous or missing matches stay `unassigned`, the mail
    room sees all candidate tokens in every case for manual assignment."""
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
