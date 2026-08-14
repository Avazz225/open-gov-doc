"""Translates a `query_language` AST into SQLAlchemy expressions (P14-S7).

Two paths, see ADR 0044 for the full rationale:

- **Pure boolean/phrase/wildcard/proximity tree (no fuzzy leaf)**: is
  composed into a SINGLE `to_tsquery('german', ...)` string (Postgres' own
  operator algebra `&`/`|`/`!`/`<->`/`<N>`/`:*` handles precedence/
  parenthesization) - ranking stays exactly as it was before this session:
  a single `ts_rank()` over the combined tsquery.
- **As soon as a fuzzy leaf occurs anywhere**: fuzzy is not part of the
  tsquery algebra (pg_trgm similarity instead of lexeme matching) - the
  whole tree is instead compiled leaf-by-leaf into SQL boolean expressions
  (`and_`/`or_`/`not_`), with each non-fuzzy leaf contributing its own
  `search_vector @@ to_tsquery(...)`. This is mathematically exactly
  equivalent to the combined-tsquery variant (Postgres' `@@` operator
  distributes correctly over `&`/`|`/`!`), just structurally split into
  several SQL conditions instead of one. Ranking is deliberately simpler on
  this path: a sum of independent per-leaf scores instead of a boolean-
  structure-aware `ts_rank` - consistent with ADR 0012's already-accepted
  "not BM25-level" limitation.
"""

from __future__ import annotations

from dataclasses import dataclass

from search_service.models import SearchDocument
from search_service.query_language import (
    FUZZY_THRESHOLDS,
    And,
    Fuzzy,
    Node,
    Not,
    Or,
    Phrase,
    Prefix,
    Proximity,
    Term,
    contains_fuzzy,
)
from sqlalchemy import ColumnElement, and_, func, literal, not_, or_


@dataclass
class CompiledQuery:
    where: ColumnElement
    rank: ColumnElement


def compile_query(node: Node) -> CompiledQuery:
    if not contains_fuzzy(node):
        tsquery = func.to_tsquery("german", _render_tsquery_string(node))
        return CompiledQuery(
            where=SearchDocument.search_vector.op("@@")(tsquery),
            rank=func.ts_rank(SearchDocument.search_vector, tsquery),
        )
    return CompiledQuery(where=_compile_bool(node), rank=_compile_rank_sum(node))


# --- Path 1: a single to_tsquery string -------------------------


def _render_tsquery_string(node: Node) -> str:
    if isinstance(node, Term):
        return node.word
    if isinstance(node, Phrase):
        return "(" + " <-> ".join(node.words) + ")"
    if isinstance(node, Prefix):
        return f"{node.word}:*"
    if isinstance(node, Proximity):
        return _proximity_string(node.word_a, node.word_b, node.distance)
    if isinstance(node, And):
        return f"({_render_tsquery_string(node.left)} & {_render_tsquery_string(node.right)})"
    if isinstance(node, Or):
        return f"({_render_tsquery_string(node.left)} | {_render_tsquery_string(node.right)})"
    if isinstance(node, Not):
        return f"!{_render_tsquery_string(node.child)}"
    raise AssertionError(f"Fuzzy-Knoten darf diesen Pfad nicht erreichen: {node!r}")


def _proximity_string(word_a: str, word_b: str, distance: int) -> str:
    """ "a"/"b" count as a match if they occur in EITHER of the two orders
    within `distance` words of each other - Postgres' `<N>` operator itself
    requires exactly N words apart AND a fixed order, hence the OR chain
    over 1..distance per direction."""
    variants = [f"{word_a}<{d}>{word_b}" for d in range(1, distance + 1)]
    variants += [f"{word_b}<{d}>{word_a}" for d in range(1, distance + 1)]
    return "(" + " | ".join(variants) + ")"


# --- Path 2: leaf-by-leaf SQL boolean expressions (contains fuzzy) -----------


def _leaf_where(node: Term | Phrase | Prefix | Proximity) -> ColumnElement:
    tsquery = func.to_tsquery("german", _render_tsquery_string(node))
    return SearchDocument.search_vector.op("@@")(tsquery)


def _fuzzy_where(node: Fuzzy) -> ColumnElement:
    threshold = FUZZY_THRESHOLDS[node.level]
    return or_(
        func.word_similarity(node.word, SearchDocument.title) >= threshold,
        func.word_similarity(node.word, func.coalesce(SearchDocument.full_text, "")) >= threshold,
    )


def _compile_bool(node: Node) -> ColumnElement:
    if isinstance(node, Fuzzy):
        return _fuzzy_where(node)
    if isinstance(node, (Term, Phrase, Prefix, Proximity)):
        return _leaf_where(node)
    if isinstance(node, And):
        return and_(_compile_bool(node.left), _compile_bool(node.right))
    if isinstance(node, Or):
        return or_(_compile_bool(node.left), _compile_bool(node.right))
    if isinstance(node, Not):
        return not_(_compile_bool(node.child))
    raise AssertionError(f"Unbekannter Knotentyp: {node!r}")


def _fuzzy_rank(node: Fuzzy) -> ColumnElement:
    return func.greatest(
        func.word_similarity(node.word, SearchDocument.title),
        func.word_similarity(node.word, func.coalesce(SearchDocument.full_text, "")),
    )


def _leaf_rank(node: Term | Phrase | Prefix | Proximity) -> ColumnElement:
    tsquery = func.to_tsquery("german", _render_tsquery_string(node))
    return func.ts_rank(SearchDocument.search_vector, tsquery)


def _compile_rank_sum(node: Node) -> ColumnElement:
    if isinstance(node, Fuzzy):
        return _fuzzy_rank(node)
    if isinstance(node, (Term, Phrase, Prefix, Proximity)):
        return _leaf_rank(node)
    if isinstance(node, (And, Or)):
        return _compile_rank_sum(node.left) + _compile_rank_sum(node.right)
    if isinstance(node, Not):
        # A negated leaf should not affect relevance.
        return literal(0.0)
    raise AssertionError(f"Unbekannter Knotentyp: {node!r}")
