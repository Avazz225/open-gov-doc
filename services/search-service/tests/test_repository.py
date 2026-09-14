import uuid
from datetime import UTC, datetime

from search_service import repository
from search_service.query_language import QuerySyntaxError
from search_service.repository import AttrFilter


def _now():
    return datetime.now(UTC)


async def _index(
    session,
    *,
    document_id=None,
    title="Rechnung",
    folder_id="f1",
    folder_name="Verträge",
    object_type_id=1,
    attributes=None,
    full_text="",
    created_by="alice",
    registered_at=None,
):
    document_id = document_id or f"doc-{uuid.uuid4().hex[:8]}"
    return await repository.upsert_document(
        session,
        document_id=document_id,
        title=title,
        folder_id=folder_id,
        folder_name=folder_name,
        object_type_id=object_type_id,
        attributes=attributes or {},
        current_version_number=1,
        full_text=full_text,
        created_by=created_by,
        created_at=_now(),
        updated_at=_now(),
        registered_at=registered_at,
    )


async def test_upsert_then_get(session):
    doc = await _index(session, title="Rechnung Nr 4711")
    await session.commit()

    fetched = await repository.get_document(session, doc.document_id)
    assert fetched is not None
    assert fetched.title == "Rechnung Nr 4711"


async def test_upsert_overwrites_same_key(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=document_id, title="Alt")
    await session.commit()

    await _index(session, document_id=document_id, title="Neu")
    await session.commit()

    fetched = await repository.get_document(session, document_id)
    assert fetched.title == "Neu"


async def test_delete_document(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=document_id)
    await session.commit()

    await repository.delete_document(session, document_id)
    await session.commit()

    assert await repository.get_document(session, document_id) is None


async def test_search_finds_document_by_title(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=document_id, title="Jahresabschluss 2026")
    await session.commit()

    rows = await repository.search(
        session,
        query="Jahresabschluss",
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    assert any(doc.document_id == document_id for doc, _rank in rows)


async def test_search_finds_document_by_full_text(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(
        session,
        document_id=document_id,
        title="Scan.pdf",
        full_text="Dieser Vertrag regelt die Zusammenarbeit zwischen den Parteien",
    )
    await session.commit()

    rows = await repository.search(
        session,
        query="Zusammenarbeit",
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    assert any(doc.document_id == document_id for doc, _rank in rows)


async def test_search_without_query_returns_all_sorted_by_updated_at(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=document_id)
    await session.commit()

    rows = await repository.search(
        session,
        query=None,
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    assert any(doc.document_id == document_id for doc, rank in rows)
    assert all(rank is None for _doc, rank in rows)


async def test_search_filters_by_string_attribute_exact_match(session):
    matching = f"doc-{uuid.uuid4().hex[:8]}"
    other = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=matching, attributes={"kunde": "Acme GmbH"})
    await _index(session, document_id=other, attributes={"kunde": "Andere Firma"})
    await session.commit()

    rows = await repository.search(
        session,
        query=None,
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[AttrFilter(name="kunde", op="eq", value="Acme GmbH", attr_type="string")],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert matching in ids
    assert other not in ids


async def test_search_filters_by_decimal_attribute_range(session):
    low = f"doc-{uuid.uuid4().hex[:8]}"
    high = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=low, attributes={"betrag": 10.0})
    await _index(session, document_id=high, attributes={"betrag": 500.0})
    await session.commit()

    rows = await repository.search(
        session,
        query=None,
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[AttrFilter(name="betrag", op="gte", value="100", attr_type="decimal")],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert high in ids
    assert low not in ids


async def test_search_filters_by_date_attribute_range(session):
    early = f"doc-{uuid.uuid4().hex[:8]}"
    late = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=early, attributes={"faelligkeit": "2026-01-01"})
    await _index(session, document_id=late, attributes={"faelligkeit": "2026-12-31"})
    await session.commit()

    rows = await repository.search(
        session,
        query=None,
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[
            AttrFilter(name="faelligkeit", op="gte", value="2026-06-01", attr_type="date")
        ],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert late in ids
    assert early not in ids


async def test_search_boolean_and(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=document_id, title="Rechnung Vertrag")
    other = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=other, title="Rechnung")
    await session.commit()

    rows = await repository.search(
        session,
        query="Rechnung Vertrag",
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert document_id in ids
    assert other not in ids


async def test_search_boolean_or(session):
    a = f"doc-{uuid.uuid4().hex[:8]}"
    b = f"doc-{uuid.uuid4().hex[:8]}"
    c = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=a, title="Rechnung")
    await _index(session, document_id=b, title="Angebot")
    await _index(session, document_id=c, title="Mahnung")
    await session.commit()

    rows = await repository.search(
        session,
        query="Rechnung or Angebot",
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert a in ids
    assert b in ids
    assert c not in ids


async def test_search_not_excludes_term(session):
    keep = f"doc-{uuid.uuid4().hex[:8]}"
    exclude = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=keep, title="Rechnung offen")
    await _index(session, document_id=exclude, title="Rechnung storniert")
    await session.commit()

    rows = await repository.search(
        session,
        query="Rechnung -storniert",
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert keep in ids
    assert exclude not in ids


async def test_search_grouping_with_parentheses(session):
    # "Rechnung and (Vertrag or Angebot)"
    a = f"doc-{uuid.uuid4().hex[:8]}"
    b = f"doc-{uuid.uuid4().hex[:8]}"
    excluded = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=a, title="Rechnung Vertrag")
    await _index(session, document_id=b, title="Rechnung Angebot")
    await _index(session, document_id=excluded, title="Rechnung Mahnung")
    await session.commit()

    rows = await repository.search(
        session,
        query="Rechnung (Vertrag or Angebot)",
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert a in ids
    assert b in ids
    assert excluded not in ids


async def test_search_phrase_requires_exact_word_order(session):
    matching = f"doc-{uuid.uuid4().hex[:8]}"
    non_matching = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=matching, title="Rechnung Nr 4711")
    await _index(session, document_id=non_matching, title="Nr 4711 Rechnung")
    await session.commit()

    rows = await repository.search(
        session,
        query='"Rechnung Nr 4711"',
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert matching in ids
    assert non_matching not in ids


async def test_search_prefix_wildcard(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=document_id, title="Rechnungswesen 2026")
    await session.commit()

    rows = await repository.search(
        session,
        query="Rechnung*",
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    assert any(doc.document_id == document_id for doc, _rank in rows)


async def test_search_fuzzy_finds_typo_tolerant_match(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=document_id, title="Rechnung")
    await session.commit()

    rows = await repository.search(
        session,
        query="Rechnnug~",  # Tippfehler
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, rank in rows}
    assert document_id in ids
    assert all(
        rank is not None and rank > 0 for _doc, rank in rows if _doc.document_id == document_id
    )


async def test_search_fuzzy_strict_level_rejects_dissimilar_word(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=document_id, title="Rechnung")
    await session.commit()

    rows = await repository.search(
        session,
        query="Apfelsine~1",  # völlig anderes Wort, auch mit strenger Stufe kein Treffer
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert document_id not in ids


async def test_search_proximity_finds_words_within_distance(session):
    close = f"doc-{uuid.uuid4().hex[:8]}"
    far = f"doc-{uuid.uuid4().hex[:8]}"
    await _index(session, document_id=close, title="Vertrag mit Kunde")
    await _index(
        session,
        document_id=far,
        title="Vertrag mit einem sehr wichtigen und lange erwarteten Kunde",
    )
    await session.commit()

    rows = await repository.search(
        session,
        query='"Vertrag Kunde"~2',
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="relevance",
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert close in ids
    assert far not in ids


async def test_search_invalid_query_raises_syntax_error(session):
    try:
        await repository.search(
            session,
            query="a (b or c",
            folder_id=None,
            object_type_id=None,
            created_by=None,
            created_after=None,
            created_before=None,
            attr_filters=[],
            limit=20,
            offset=0,
            sort="relevance",
        )
        raise AssertionError("Erwarteter QuerySyntaxError wurde nicht geworfen")
    except QuerySyntaxError:
        pass


async def test_facet_counts_groups_by_folder(session):
    await _index(session, folder_id="fa", folder_name="Ordner A")
    await _index(session, folder_id="fa", folder_name="Ordner A")
    await _index(session, folder_id="fb", folder_name="Ordner B")
    await session.commit()

    facets = await repository.facet_counts(
        session,
        query=None,
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
    )

    counts = {row["folder_id"]: row["count"] for row in facets["folder"]}
    assert counts.get("fa") == 2


# Work-tray cross-installation browse (ADR 0113/0118/0146).


async def test_search_registered_false_lists_only_unregistered_documents(session):
    draft = await _index(session, registered_at=None)
    await _index(session, registered_at=_now())
    await session.commit()

    rows = await repository.search(
        session,
        query=None,
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="updated_at",
        registered=False,
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert ids == {draft.document_id}


async def test_search_registered_true_lists_only_registered_documents(session):
    await _index(session, registered_at=None)
    registered = await _index(session, registered_at=_now())
    await session.commit()

    rows = await repository.search(
        session,
        query=None,
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="updated_at",
        registered=True,
    )

    ids = {doc.document_id for doc, _rank in rows}
    assert ids == {registered.document_id}


async def test_search_without_registered_filter_returns_both(session):
    await _index(session, registered_at=None)
    await _index(session, registered_at=_now())
    await session.commit()

    rows = await repository.search(
        session,
        query=None,
        folder_id=None,
        object_type_id=None,
        created_by=None,
        created_after=None,
        created_before=None,
        attr_filters=[],
        limit=20,
        offset=0,
        sort="updated_at",
    )

    assert len(rows) == 2


# Hand-folder cross-index (ADR 0118/0146).


async def test_upsert_folder_reference_then_list(session):
    await repository.upsert_folder_reference(
        session,
        folder_id="hf1",
        document_id="doc-1",
        folder_name="Handakte Müller",
        added_by="alice",
        added_at=_now(),
    )
    await session.commit()

    rows = await repository.list_folder_references(session, limit=20, offset=0)
    assert len(rows) == 1
    ref, title = rows[0]
    assert ref.folder_id == "hf1"
    assert ref.document_id == "doc-1"
    assert ref.folder_name == "Handakte Müller"
    assert title is None  # document not indexed here, LEFT JOIN yields None


async def test_upsert_folder_reference_joins_document_title(session):
    doc = await _index(session, title="Rechnung Nr 42")
    await session.commit()

    await repository.upsert_folder_reference(
        session,
        folder_id="hf1",
        document_id=doc.document_id,
        folder_name="Handakte Müller",
        added_by="alice",
        added_at=_now(),
    )
    await session.commit()

    rows = await repository.list_folder_references(session, limit=20, offset=0)
    _ref, title = rows[0]
    assert title == "Rechnung Nr 42"


async def test_upsert_folder_reference_same_pair_overwrites_not_duplicates(session):
    await repository.upsert_folder_reference(
        session,
        folder_id="hf1",
        document_id="doc-1",
        folder_name="Alt",
        added_by="alice",
        added_at=_now(),
    )
    await session.commit()

    await repository.upsert_folder_reference(
        session,
        folder_id="hf1",
        document_id="doc-1",
        folder_name="Neu",
        added_by="bob",
        added_at=_now(),
    )
    await session.commit()

    rows = await repository.list_folder_references(session, limit=20, offset=0)
    assert len(rows) == 1
    assert rows[0][0].folder_name == "Neu"
    assert rows[0][0].added_by == "bob"


async def test_delete_folder_reference_removes_row(session):
    await repository.upsert_folder_reference(
        session,
        folder_id="hf1",
        document_id="doc-1",
        folder_name="Handakte Müller",
        added_by="alice",
        added_at=_now(),
    )
    await session.commit()

    await repository.delete_folder_reference(session, "hf1", "doc-1")
    await session.commit()

    rows = await repository.list_folder_references(session, limit=20, offset=0)
    assert rows == []


async def test_list_folder_references_filters_by_folder_id(session):
    await repository.upsert_folder_reference(
        session,
        folder_id="hf1",
        document_id="doc-1",
        folder_name=None,
        added_by="a",
        added_at=_now(),
    )
    await repository.upsert_folder_reference(
        session,
        folder_id="hf2",
        document_id="doc-2",
        folder_name=None,
        added_by="a",
        added_at=_now(),
    )
    await session.commit()

    rows = await repository.list_folder_references(session, folder_id="hf1", limit=20, offset=0)
    assert [ref.folder_id for ref, _title in rows] == ["hf1"]


async def test_list_folder_references_filters_by_document_id(session):
    await repository.upsert_folder_reference(
        session,
        folder_id="hf1",
        document_id="doc-1",
        folder_name=None,
        added_by="a",
        added_at=_now(),
    )
    await repository.upsert_folder_reference(
        session,
        folder_id="hf2",
        document_id="doc-1",
        folder_name=None,
        added_by="a",
        added_at=_now(),
    )
    await repository.upsert_folder_reference(
        session,
        folder_id="hf1",
        document_id="doc-2",
        folder_name=None,
        added_by="a",
        added_at=_now(),
    )
    await session.commit()

    rows = await repository.list_folder_references(session, document_id="doc-1", limit=20, offset=0)
    assert {ref.folder_id for ref, _title in rows} == {"hf1", "hf2"}
