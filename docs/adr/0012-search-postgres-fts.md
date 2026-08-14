# 0012 — Search: Postgres full-text search instead of a dedicated search index

**Status:** accepted
**Context:** Concept 3.7, Session P5-S4

## Decision

The new `search-service` implements "full-text index + faceted search" (3.7)
via **Postgres's built-in full-text search** instead of a dedicated search
index (Elasticsearch/Meilisearch/OpenSearch): a `tsvector` column
(`search_vector`) per indexed document, built from
`setweight(to_tsvector('german', title), 'A') ||
setweight(to_tsvector('german', full_text), 'B')` (title matches weighted
higher than full-text matches), GIN-indexed, queried via
`websearch_to_tsquery('german', ...)` and `ts_rank(...)` for relevance
ranking.

## Rationale

- **Same class of trade-off as ADR 0010 (EicarSignatureEngine instead of
  ClamdEngine) and ADR 0011 (Tesseract instead of PaddleOCR)**: a dedicated
  search index means another container, more operational surface, extra
  storage/startup-time overhead for a reproducible `docker compose up
  --build` - avoided when an already-available, native alternative
  suffices.
- **Postgres comes with a built-in `german` text search configuration**
  (stemming, stop words) - no additional extension needed. `pg_trgm`/
  `unaccent` are available in the running Postgres instance
  (`pg_available_extensions`), but are deliberately **not** installed/wired
  up this session: the concept only requires "full-text index + faceted
  search", not typo tolerance - both remain documented as an obvious, cheap
  future extension (`CREATE EXTENSION pg_trgm`, `similarity()` fallback on
  weak `ts_rank` matches), but are not part of this session.
- **Postgres is already the central database of every service in this
  system** (3.1: one schema per service) - no new infrastructure component,
  no additional synchronization logic between two storage systems.
- **Faceted search (object type/folder/attributes/creator/date) is fully
  expressible with plain SQL `WHERE` clauses and `GROUP BY` aggregation** -
  no need for a specialized facet engine for the scope required here.

## Consequences

- Relevance ranking is solid, but not at the level of BM25/specialized
  ranking algorithms of dedicated search engines - sufficient for the scope
  of this session (full-text index + facets).
- ~~No typo tolerance/fuzzy matching in this session (`pg_trgm` would be the
  obvious extension path, see above).~~ Closed in P14-S7: `pg_trgm` has
  since been installed, `query_language.py`/`query_compiler.py`
  additionally build fuzzy and proximity search as well as wildcards on top
  - see [ADR 0044](0044-search-query-language-fuzzy-proximity.md).
- Attribute filters on the JSONB `attributes` field are limited to simple
  exact/range comparisons (`->>` text extraction + type cast) - no complex
  nested attribute structures, but this matches the current, flat object
  type attribute schema (2.2).
- A switch to a dedicated search index remains possible should scaling or
  relevance requirements later demand it - the indexing pipeline
  (`consumer.py`/`pipeline.py`) is already separated from the storage
  mechanism (`repository.py`).
