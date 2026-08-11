from mail_connector import matching


def test_extract_candidates_finds_dash_separated_token():
    candidates = matching.extract_candidates("Betreff: Rueckmeldung zu Az: 2026-001 vielen Dank")
    assert "2026-001" in candidates


def test_extract_candidates_deduplicates_and_preserves_order():
    candidates = matching.extract_candidates("2026-001 irgendwas 2026-001 dann 2026-002")
    assert candidates == ["2026-001", "2026-002"]


def test_extract_candidates_ignores_plain_words():
    candidates = matching.extract_candidates("Sehr geehrte Damen und Herren, vielen Dank.")
    assert candidates == []


class _FakeDocumentClient:
    def __init__(self, hits: dict[str, list[dict]]):
        self._hits = hits

    async def lookup_by_kennzeichen(self, value):
        return self._hits.get(value, [])


class _FakeCaseClient:
    def __init__(self, hits: dict[str, list[dict]]):
        self._hits = hits

    async def lookup_by_vorgangsnummer(self, value):
        return self._hits.get(value, [])


async def test_resolve_match_returns_unique_document_hit():
    documents = _FakeDocumentClient({"2026-001": [{"id": "doc-1"}]})
    cases = _FakeCaseClient({})

    result = await matching.resolve_match(
        "Az: 2026-001", document_client=documents, case_client=cases
    )

    assert result.match_type == "kennzeichen"
    assert result.match_value == "2026-001"
    assert result.target_type == "document"
    assert result.target_id == "doc-1"


async def test_resolve_match_returns_unique_case_hit():
    documents = _FakeDocumentClient({})
    cases = _FakeCaseClient({"2026-007": [{"id": "case-7"}]})

    result = await matching.resolve_match(
        "Vorgang 2026-007", document_client=documents, case_client=cases
    )

    assert result.target_type == "case"
    assert result.target_id == "case-7"


async def test_resolve_match_stays_unassigned_without_hit():
    documents = _FakeDocumentClient({})
    cases = _FakeCaseClient({})

    result = await matching.resolve_match(
        "Az: 2026-999 keine Ahnung", document_client=documents, case_client=cases
    )

    assert result.target_type is None
    assert result.candidates == ["2026-999"]


async def test_resolve_match_stays_unassigned_on_ambiguous_hit():
    documents = _FakeDocumentClient({"2026-001": [{"id": "doc-1"}]})
    cases = _FakeCaseClient({"2026-001": [{"id": "case-1"}]})

    result = await matching.resolve_match("2026-001", document_client=documents, case_client=cases)

    assert result.target_type is None
    assert result.match_type is None
