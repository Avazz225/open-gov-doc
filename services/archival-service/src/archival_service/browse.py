import logging
from datetime import UTC, datetime, timedelta

from archival_service.models import ArchivalTransfer, CaseArchivalTransfer

logger = logging.getLogger(__name__)

_MIN_DATETIME = datetime.min.replace(tzinfo=UTC)


def _matches(query: str, title: str, identifier: str | None) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    haystack = f"{title} {identifier or ''}".lower()
    return needle in haystack


async def build_released_items(
    document_transfers: list[ArchivalTransfer],
    case_transfers: list[CaseArchivalTransfer],
    *,
    document_client,
    case_client,
    dehydration_delay_days: int,
    query: str | None = None,
) -> list[dict]:
    """Aussonderungs-Zugriffsbereich (2.5/5.6, P15-S5) - hydratisiert die
    bereits bestehenden `released`-Transfers (kein neuer Datenspeicher, siehe
    IMPLEMENTATION_PLAN.md) mit den zugehörigen Dokument-/Umlaufmappen-
    Metadaten, damit ausgesonderte, aber noch innerhalb der Übergangsfrist
    befindliche Elemente direkt durchsuch-/einsehbar sind statt nur indirekt
    über Audit-Trail-Verweise (Konzept 5.6, wörtlich der Auftrag dieser
    Session). Ein einzelner nicht mehr auflösbarer Verweis (z. B. inzwischen
    hart gelöschtes Dokument) blockiert die übrigen Treffer nicht - gleiches
    Fehlertoleranz-Prinzip wie `directory_federation.search_all_peers`."""
    items: list[dict] = []

    for transfer in document_transfers:
        try:
            document = await document_client.get_document(transfer.document_id)
        except Exception:
            logger.warning(
                "Dokument %s zu Transfer %s nicht auflösbar - übersprungen.",
                transfer.document_id,
                transfer.id,
            )
            continue
        title = document.get("title") or transfer.document_id
        identifier = (document.get("attributes") or {}).get("Kennzeichen")
        if not _matches(query or "", title, identifier):
            continue
        purge_at: datetime | None = None
        if transfer.released_at is not None:
            purge_at = transfer.released_at + timedelta(days=dehydration_delay_days)
        items.append(
            {
                "transfer_id": transfer.id,
                "kind": "document",
                "subject_id": transfer.document_id,
                "title": title,
                "identifier": identifier,
                "released_at": transfer.released_at,
                "purge_at": purge_at,
            }
        )

    for transfer in case_transfers:
        try:
            case = await case_client.get_case(transfer.case_id)
        except Exception:
            logger.warning(
                "Umlaufmappe %s zu Transfer %s nicht auflösbar - übersprungen.",
                transfer.case_id,
                transfer.id,
            )
            continue
        title = case.get("name") or transfer.case_id
        identifier = case.get("vorgangsnummer")
        if not _matches(query or "", title, identifier):
            continue
        items.append(
            {
                "transfer_id": transfer.id,
                "kind": "case",
                "subject_id": transfer.case_id,
                "title": title,
                "identifier": identifier,
                "released_at": transfer.released_at,
                # Kein `dehydrated`-Zustand fuer Umlaufmappen (siehe models.py
                # `CaseArchivalTransfer`-Docstring) - es gibt daher keinen
                # automatischen Zeitpunkt, an dem ein Case aus diesem Bereich
                # "verschwindet" (Konzept 5.6). Bewusst offener Punkt, siehe
                # ADR 0055 statt eines hier erfundenen Ablaufdatums.
                "purge_at": None,
            }
        )

    items.sort(key=lambda item: item["released_at"] or _MIN_DATETIME, reverse=True)
    return items
