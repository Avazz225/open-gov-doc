from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from search_service.models import SearchDocument
from sqlalchemy import Date, Numeric, cast, delete, func, null, select, text
from sqlalchemy.ext.asyncio import AsyncSession


class NotFoundError(Exception):
    pass


@dataclass
class AttrFilter:
    """Ein Attributfilter aus der `attr.{name}[.gte|.lte]=`-Query-Param-
    Konvention (siehe main.py), aufgelöst gegen das Objekttyp-Schema (Konzept
    2.2/4.5: string/decimal/integer/boolean/date/reference)."""

    name: str
    op: Literal["eq", "gte", "lte"]
    value: str
    attr_type: str


async def upsert_document(
    session: AsyncSession,
    *,
    document_id: str,
    title: str,
    folder_id: str | None,
    folder_name: str | None,
    object_type_id: int | None,
    attributes: dict,
    current_version_number: int,
    full_text: str,
    created_by: str,
    created_at: datetime,
    updated_at: datetime,
) -> SearchDocument:
    """Legt einen Index-Eintrag an oder aktualisiert ihn (natürlicher
    Primärschlüssel `document_id`, macht erneutes Indizieren idempotent).
    `search_vector` wird bewusst per Roh-SQL-UPDATE nach dem Flush berechnet
    (setweight: Titel > Volltext) statt als generierte Postgres-Spalte - hält
    die Gewichtungslogik hier sichtbar/testbar statt in der DDL versteckt."""
    doc = await session.get(SearchDocument, document_id)
    if doc is None:
        doc = SearchDocument(document_id=document_id, search_vector="")
        session.add(doc)
    doc.title = title
    doc.folder_id = folder_id
    doc.folder_name = folder_name
    doc.object_type_id = object_type_id
    doc.attributes = attributes
    doc.current_version_number = current_version_number
    doc.full_text = full_text
    doc.created_by = created_by
    doc.created_at = created_at
    doc.updated_at = updated_at
    doc.indexed_at = datetime.now(UTC)
    await session.flush()

    await session.execute(
        text(
            "UPDATE search.search_document "
            "SET search_vector = "
            "setweight(to_tsvector('german', title), 'A') || "
            "setweight(to_tsvector('german', coalesce(full_text, '')), 'B') "
            "WHERE document_id = :document_id"
        ),
        {"document_id": document_id},
    )
    await session.refresh(doc)
    return doc


async def delete_document(session: AsyncSession, document_id: str) -> None:
    await session.execute(delete(SearchDocument).where(SearchDocument.document_id == document_id))


async def get_document(session: AsyncSession, document_id: str) -> SearchDocument | None:
    return await session.get(SearchDocument, document_id)


def _apply_common_filters(
    stmt,
    *,
    folder_id: str | None,
    object_type_id: int | None,
    created_by: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    attr_filters: list[AttrFilter],
):
    if folder_id is not None:
        stmt = stmt.where(SearchDocument.folder_id == folder_id)
    if object_type_id is not None:
        stmt = stmt.where(SearchDocument.object_type_id == object_type_id)
    if created_by is not None:
        stmt = stmt.where(SearchDocument.created_by == created_by)
    if created_after is not None:
        stmt = stmt.where(SearchDocument.created_at >= created_after)
    if created_before is not None:
        stmt = stmt.where(SearchDocument.created_at <= created_before)
    for attr_filter in attr_filters:
        field = SearchDocument.attributes[attr_filter.name].astext
        if attr_filter.attr_type in ("decimal", "integer"):
            casted = cast(field, Numeric)
            value = float(attr_filter.value)
        elif attr_filter.attr_type == "date":
            casted = cast(field, Date)
            value = date.fromisoformat(attr_filter.value)
        else:  # string, boolean, reference - exact match on the text representation
            casted = field
            value = attr_filter.value
        if attr_filter.op == "eq":
            stmt = stmt.where(casted == value)
        elif attr_filter.op == "gte":
            stmt = stmt.where(casted >= value)
        else:
            stmt = stmt.where(casted <= value)
    return stmt


async def search(
    session: AsyncSession,
    *,
    query: str | None,
    folder_id: str | None,
    object_type_id: int | None,
    created_by: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    attr_filters: list[AttrFilter],
    limit: int,
    offset: int,
    sort: Literal["relevance", "created_at", "updated_at"],
) -> list[tuple[SearchDocument, float | None]]:
    """Liefert `(SearchDocument, rank)`-Paare, `rank` nur gesetzt wenn `query`
    angegeben ist. Ergebnisse werden VOR jeglicher Berechtigungsprüfung
    zurückgegeben - die Filterung nach `folder_id`-Zugriff passiert im
    Routen-Handler (main.py), da sie einen Aufruf an den Permission Service
    braucht (3.1: kein direkter Cross-Service-Datenbankzugriff)."""
    rank = None
    if query:
        ts_query = func.websearch_to_tsquery("german", query)
        rank = func.ts_rank(SearchDocument.search_vector, ts_query)
        stmt = select(SearchDocument, rank).where(SearchDocument.search_vector.op("@@")(ts_query))
    else:
        stmt = select(SearchDocument, null())

    stmt = _apply_common_filters(
        stmt,
        folder_id=folder_id,
        object_type_id=object_type_id,
        created_by=created_by,
        created_after=created_after,
        created_before=created_before,
        attr_filters=attr_filters,
    )

    if query and sort in ("relevance", None):
        stmt = stmt.order_by(rank.desc())
    elif sort == "created_at":
        stmt = stmt.order_by(SearchDocument.created_at.desc())
    else:
        stmt = stmt.order_by(SearchDocument.updated_at.desc())

    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def facet_counts(
    session: AsyncSession,
    *,
    query: str | None,
    folder_id: str | None,
    object_type_id: int | None,
    created_by: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    attr_filters: list[AttrFilter],
) -> dict:
    """Gruppierte Trefferzahlen über dieselbe (vor-Berechtigungsfilterung)
    Treffermenge wie `search()` - bewusst einfach: keine "Facette ohne
    Selbstfilter"-Logik wie bei größeren Suchsystemen, das wäre Overengineering
    für den hier verlangten Umfang ("Volltextindex + Facettensuche")."""
    base = select(SearchDocument.folder_id, SearchDocument.folder_name, func.count())
    if query:
        base = base.where(
            SearchDocument.search_vector.op("@@")(func.websearch_to_tsquery("german", query))
        )
    base = _apply_common_filters(
        base,
        folder_id=folder_id,
        object_type_id=object_type_id,
        created_by=created_by,
        created_after=created_after,
        created_before=created_before,
        attr_filters=attr_filters,
    ).group_by(SearchDocument.folder_id, SearchDocument.folder_name)
    folder_rows = (await session.execute(base)).all()

    base_ot = select(SearchDocument.object_type_id, func.count())
    if query:
        base_ot = base_ot.where(
            SearchDocument.search_vector.op("@@")(func.websearch_to_tsquery("german", query))
        )
    base_ot = _apply_common_filters(
        base_ot,
        folder_id=folder_id,
        object_type_id=object_type_id,
        created_by=created_by,
        created_after=created_after,
        created_before=created_before,
        attr_filters=attr_filters,
    ).group_by(SearchDocument.object_type_id)
    object_type_rows = (await session.execute(base_ot)).all()

    return {
        "folder": [
            {"folder_id": fid, "folder_name": fname, "count": count}
            for fid, fname, count in folder_rows
        ],
        "object_type": [
            {"object_type_id": otid, "count": count} for otid, count in object_type_rows
        ],
    }
