# 0044 — Extended search query language: dedicated parser instead of websearch_to_tsquery, fuzzy/proximity via pg_trgm + tsquery distance operators

**Status:** accepted
**Context:** Concept 3.7a, session P14-S7 (closes the point deliberately left open in [ADR 0012](0012-search-postgres-fts.md))

## Decision

`search-service` replaces the previous direct `websearch_to_tsquery(query)` call with its own small, testable parser (`query_language.py`) for a query syntax modeled on common reference products:

```
wort1 wort2        implicit AND
wort1 OR wort2      disjunction
-wort / NOT wort    negation
(...)               explicit grouping/precedence
"genaue phrase"     phrase search
"wort1 wort2"~N     proximity search (exactly two words, distance ≤ N, both orders)
wort*               wildcard/prefix search
wort~ / wort~N      fuzzy search, tolerance level N ∈ {1,2,3} (default 2)
```

A `query_compiler.py` translates the resulting AST into SQLAlchemy expressions: the pure boolean/phrase/wildcard/proximity portion is assembled into a SINGLE `to_tsquery('german', ...)` string (Postgres' own `&`/`|`/`!`/`<->`/`<N>`/`:*` algebra takes over precedence and grouping); fuzzy leaves are not part of this algebra and are compiled as a separate SQL condition via `pg_trgm`'s `word_similarity()`.

## Rationale

- **Why a dedicated parser instead of continuing to use `websearch_to_tsquery`**: `websearch_to_tsquery` already covers AND/OR/NOT/phrase (ADR 0012), but knows neither wildcards nor any form of fuzzy/proximity search - neither is provided for in its grammar and cannot be smuggled into the same function call after the fact. A dedicated, small recursive-descent parser (~250 lines including tokenizer) is the more direct, more testable solution than trying to somehow funnel wildcard/fuzzy syntax through `websearch_to_tsquery`.
- **Why a single composed `to_tsquery` string instead of SQLAlchemy operator composition across multiple `to_tsquery()` calls**: Postgres' `to_tsquery(config, text)` itself parses boolean operators/parentheses/distance operators/prefix flags out of the given string, and in doing so normalizes every plain word through the German dictionary (stemming) - identical behavior to `websearch_to_tsquery`, just with the full operator range. The compiler therefore only needs to build a correctly parenthesized string (recursively, each subtree gets its own parentheses), not reimplement an SQLAlchemy-side operator algebra (`&&`/`||`/`!!`). Since the tokenizer restricts word tokens to alphanumeric characters plus hyphen/underscore (no tsquery metacharacters `&|!<>():` possible), there is no escaping/injection risk even though the string is assembled from user input - it is passed as ONE bound parameter regardless, never interpolated into raw SQL.
- **Proximity search via `<N>` instead of a Lucene-style sloppy-phrase function**: Postgres' `<N>` operator itself requires an EXACT distance AND a fixed order ("fat <2> rats" = exactly 2 lexemes in between, in that direction). "Within N words, either order" (concept wording: "two terms ... when they occur within a configurable word distance of each other") is therefore built as an OR chain over distances 1..N in both directions (`a<1>b | a<2>b | ... | b<1>a | ...`) - mathematically exactly the desired result, no compromise, just structurally a disjunction rather than a single operator. `MAX_PROXIMITY_DISTANCE = 20` deliberately bounds the chain length (the same kind of limit as `search_result_hard_limit`), a larger `~N` is silently clamped rather than rejected.
- **Fuzzy search via `pg_trgm`'s `word_similarity()`, not `similarity()`**: `similarity(a, b)` compares two strings as wholes - unsuitable for "does `full_text`/`title` contain a word similar to the search term" (a long full text almost never has high overall trigram similarity to a single word). `word_similarity(word, text)` is designed exactly for this case: it finds the best substring match WITHIN the text and scores that. A dedicated, documented mapping of the concept's three "tolerance levels" onto `pg_trgm`'s 0..1 floating-point scale (`FUZZY_THRESHOLDS = {1: 0.5, 2: 0.35, 3: 0.2}`, 1 = strict, 3 = tolerant) - not a Postgres convention, a deliberate calibration of our own.
- **Why fuzzy CANNOT be embedded in the same tsquery string**: `to_tsquery`'s mini-language knows only lexeme matching (exact/prefix/distance), no similarity threshold - fuzzy is conceptually a different kind of condition (trigram similarity rather than the occurrence of a normalized lexeme). A search tree that contains fuzzy is therefore compiled entirely, leaf by leaf, into SQL boolean expressions (`and_`/`or_`/`not_`), even for its non-fuzzy leaves - this is **mathematically exactly equivalent** to the combined-string variant (Postgres' `@@` operator provably distributes correctly over `&`/`|`/`!`: `A @@ (q1 & q2) ≡ (A @@ q1) AND (A @@ q2)`, analogously for `|`/`!`), just structurally split into several `@@` conditions instead of one - not an approximation, just a different compilation path that is necessary for the fuzzy case.
- **Ranking in the fuzzy path is deliberately simplified**: a sum of independent per-leaf scores (`ts_rank` per non-fuzzy leaf + `word_similarity` score per fuzzy leaf, `NOT` leaves contribute 0) instead of a boolean-structure-aware `ts_rank` over a single combined tsquery. The pure, fuzzy-free path (the overwhelming common case), by contrast, remains exactly at the previous behavior (a single `ts_rank()` over the combined tsquery) - no behavior change for existing simple queries. The simplification in the fuzzy path is consistent with ADR 0012's already-accepted limitation ("not at BM25 level").
- **No external search-engine add-on**: `pg_trgm`, as anticipated in ADR 0012, is a standard contrib module, present in the `postgres:16-alpine` image with no additional build step (`CREATE EXTENSION IF NOT EXISTS pg_trgm`, idempotent in the lifespan like every other ad-hoc schema change in this project) - confirmed consistent with the foundational decision in 3.1/ADR 0012 to use Postgres as the sole data store.
- **Malformed queries (unclosed parenthesis/quote, invalid fuzzy level, proximity search with ≠2 words) raise `QuerySyntaxError`**, translated by `main.py` into a `400` with a plain-text error message - unlike `websearch_to_tsquery`, which never raises a SQL error, but interprets any input permissively. A parser with an explicit grammar can (and should) tell the user when their query does not match the documented syntax, instead of silently interpreting it differently than intended.

## Consequences

- **New dependency `pg_trgm`** for `search-service` (already documented as the obvious extension path in ADR 0012, now implemented) - two additional trigram GIN indexes (`title`, `full_text`) alongside the existing `search_vector` GIN index.
- **Proximity search is limited to exactly two words** (concept wording "two terms") - a general N-word sloppy phrase (like Elasticsearch/Lucene's `"a b c"~5`) is deliberately not built, a `400` error message explains the limitation.
- **Very short wildcard prefixes (`a*`) can match many lexemes** and are potentially slower than a longer prefix - no minimum length is enforced (no concept requirement for one, deliberately not artificially restricted).
- **Ranking on mixed fuzzy queries is a simple sum**, not a boolean-structure-aware weighting - sufficient for the scope required here, see rationale above.
- **`OR`/`NOT` are reserved, case-insensitive keywords**, which means: searching for the actual words "or"/"not" (without intending them as an operator) is not directly possible - the same limitation common to practically every search language with boolean keywords (Lucene, Google).
- A later switch to a dedicated search index remains possible without change (see ADR 0012) - `query_language.py`/`query_compiler.py` are purely `search-service`-internal modules, with no coupling to other services.
