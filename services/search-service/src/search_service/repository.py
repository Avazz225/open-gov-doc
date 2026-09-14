from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from search_service.models import FolderReference, SearchDocument
from search_service.query_compiler import compile_query
from search_service.query_language import parse_query
from sqlalchemy import Date, Numeric, cast, delete, func, null, select, text
from sqlalchemy.ext.asyncio import AsyncSession


class NotFoundError(Exception):
    pass


@dataclass
class AttrFilter:
    """An attribute filter from the `attr.{name}[.gte|.lte]=` query-param
    convention (see main.py), resolved against the object type schema
    (concept 2.2/4.5: string/decimal/integer/boolean/date/reference)."""

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
    registered_at: datetime | None = None,
    records_quarantine_active: bool = False,
) -> SearchDocument:
    """Creates or updates an index entry (natural primary key
    `document_id`, makes re-indexing idempotent). `search_vector` is
    deliberately computed via a raw SQL UPDATE after the flush (setweight:
    title > full text) instead of as a generated Postgres column - keeps
    the weighting logic visible/testable here instead of hidden in the
    DDL."""
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
    doc.registered_at = registered_at
    doc.records_quarantine_active = records_quarantine_active
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
    registered: bool | None = None,
):
    # Records quarantine (ADR 0116, Post-Roadmap Phase 36 Session 2) -
    # unconditional, no opt-in query param: a quarantined document is never
    # returned by search/facets, the same restricted visibility
    # document-service's own folder listing already enforces. Closes the
    # gap ADR 0116 explicitly named ("a quarantined document remains fully
    # findable via search").
    stmt = stmt.where(SearchDocument.records_quarantine_active.is_(False))
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
    if registered is not None:
        # Work tray browsing (ADR 0113/0118, denormalized here since ADR
        # 0146): `registered=false` lists still-unregistered documents
        # across every folder in the installation, `true` the opposite.
        stmt = stmt.where(
            SearchDocument.registered_at.is_(None)
            if not registered
            else SearchDocument.registered_at.is_not(None)
        )
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
    registered: bool | None = None,
) -> list[tuple[SearchDocument, float | None]]:
    """Returns `(SearchDocument, rank)` pairs, `rank` only set when `query`
    is given. Results are returned BEFORE any permission check - filtering
    by `folder_id` access happens in the route handler (main.py), since it
    requires a call to the Permission Service (3.1: no direct cross-service
    database access).

    `query` is translated via `query_language.parse_query` into an AST
    (boolean combination/parenthesization/phrases/wildcards/fuzzy/proximity
    search, concept 3.7a, P14-S7) - may raise
    `query_language.QuerySyntaxError`, translated by `main.py` into a `400`
    response."""
    rank = None
    node = parse_query(query)
    if node is not None:
        compiled = compile_query(node)
        rank = compiled.rank
        stmt = select(SearchDocument, rank).where(compiled.where)
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
        registered=registered,
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
    """Grouped hit counts over the same (pre-permission-filtering) result
    set as `search()` - deliberately simple: no "facet without self-filter"
    logic like larger search systems have, that would be overengineering
    for the scope required here ("full-text index + facet search")."""
    node = parse_query(query)
    where = compile_query(node).where if node is not None else None

    base = select(SearchDocument.folder_id, SearchDocument.folder_name, func.count())
    if where is not None:
        base = base.where(where)
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
    if where is not None:
        base_ot = base_ot.where(where)
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


async def upsert_folder_reference(
    session: AsyncSession,
    *,
    folder_id: str,
    document_id: str,
    folder_name: str | None,
    added_by: str,
    added_at: datetime,
) -> FolderReference:
    """Hand-folder cross-index (ADR 0118/0146). Natural composite key
    `(folder_id, document_id)` - re-adding the same pair (the source table
    itself allows a duplicate reference row, see ADR 0118) simply refreshes
    this one row instead of creating a second, indistinguishable index
    entry, an accepted simplification for a browse aid (see ADR 0146)."""
    ref = await session.get(FolderReference, (folder_id, document_id))
    if ref is None:
        ref = FolderReference(folder_id=folder_id, document_id=document_id)
        session.add(ref)
    ref.folder_name = folder_name
    ref.added_by = added_by
    ref.added_at = added_at
    ref.indexed_at = datetime.now(UTC)
    await session.flush()
    return ref


async def delete_folder_reference(session: AsyncSession, folder_id: str, document_id: str) -> None:
    await session.execute(
        delete(FolderReference).where(
            FolderReference.folder_id == folder_id, FolderReference.document_id == document_id
        )
    )


async def list_folder_references(
    session: AsyncSession,
    *,
    folder_id: str | None = None,
    document_id: str | None = None,
    limit: int,
    offset: int,
) -> list[tuple[FolderReference, str | None]]:
    """Returns `(FolderReference, document_title)` pairs - `document_title`
    via a `LEFT JOIN` onto `SearchDocument` (so a reference to a document
    search-service hasn't indexed yet, e.g. still mid-backfill, still
    appears, just without a title). Returned BEFORE any permission check,
    same split as `search()` above - `main.py` filters by the referenced
    folder's own `folder.read` permission, the check this whole feature is
    gated behind at the source (ADR 0118)."""
    stmt = (
        select(FolderReference, SearchDocument.title)
        .outerjoin(SearchDocument, FolderReference.document_id == SearchDocument.document_id)
        .order_by(FolderReference.added_at.desc())
    )
    if folder_id is not None:
        stmt = stmt.where(FolderReference.folder_id == folder_id)
    if document_id is not None:
        stmt = stmt.where(FolderReference.document_id == document_id)
    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]
