import re
from dataclasses import dataclass

from mail_connector.case_client import CaseClient
from mail_connector.document_client import DocumentClient

# Generischer Kandidaten-Muster (2.5/3.3, P15-S3): erkennt Token der Form
# "<alnum>-<alnum>"/"<alnum>/<alnum>" (2-10 Zeichen je Seite) - deckt beide
# Default-Formate `{YYYY}-{Laufende_Nummer}` (Kennzeichen, object-type-
# service) und `{YYYY}-{Laufende_Nummer}` (Vorgangsnummer, case-service)
# ab, z. B. "2026-001". Bewusst KEIN aus den tatsächlich konfigurierten
# Formaten abgeleitetes Muster (das würde einen Cross-Service-Aufruf vor
# jeder Kandidatenextraktion erfordern) - eine Installation mit stark
# abweichenden Formaten muss dieses Muster ggf. anpassen, siehe "Offene
# Punkte" in docs/services/mail-connector.md.
_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9]{2,10}[-/][A-Za-z0-9]{2,10}\b")


@dataclass
class MatchResult:
    candidates: list[str]
    match_type: str | None  # "kennzeichen" | "vorgangsnummer"
    match_value: str | None
    target_type: str | None  # "document" | "case"
    target_id: str | None


def extract_candidates(text: str) -> list[str]:
    # Reihenfolge- und duplikatfrei (dict.fromkeys statt set) - deterministische
    # Ergebnisse für Tests/Nachvollziehbarkeit.
    return list(dict.fromkeys(_CANDIDATE_RE.findall(text)))


async def resolve_match(
    text: str, *, document_client: DocumentClient, case_client: CaseClient
) -> MatchResult:
    """Sucht in Betreff+Text nach einem eindeutigen Kennzeichen-/
    Vorgangsnummer-Treffer (2.5/10.3) - mehrdeutige oder fehlende Treffer
    bleiben `unassigned`, die Poststelle sieht in jedem Fall alle
    Kandidaten-Token für die manuelle Zuordnung."""
    candidates = extract_candidates(text)
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
