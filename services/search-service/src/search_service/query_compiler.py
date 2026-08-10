"""Übersetzt einen `query_language`-AST in SQLAlchemy-Ausdrücke (P14-S7).

Zwei Pfade, siehe ADR 0044 für die vollständige Begründung:

- **Reiner Boolescher/Phrasen/Wildcard/Näherungs-Baum (kein Fuzzy-Blatt)**:
  wird zu EINEM einzigen `to_tsquery('german', ...)`-String zusammengesetzt
  (Postgres' eigene Operator-Algebra `&`/`|`/`!`/`<->`/`<N>`/`:*` übernimmt
  Vorrang/Klammerung) - Ranking bleibt exakt wie vor dieser Session ein
  einziges `ts_rank()` über die kombinierte tsquery.
- **Sobald irgendwo ein Fuzzy-Blatt vorkommt**: Fuzzy ist kein Bestandteil
  der tsquery-Algebra (pg_trgm-Ähnlichkeit statt Lexem-Matching) - der ganze
  Baum wird stattdessen blattweise zu SQL-Booleschen Ausdrücken kompiliert
  (`and_`/`or_`/`not_`), jedes Nicht-Fuzzy-Blatt liefert dabei sein eigenes
  `search_vector @@ to_tsquery(...)`. Das ist mathematisch exakt äquivalent
  zur kombinierten-tsquery-Variante (Postgres' `@@`-Operator verteilt sich
  korrekt über `&`/`|`/`!`), nur strukturell in mehrere SQL-Bedingungen
  aufgeteilt statt einer. Das Ranking ist in diesem Pfad bewusst einfacher:
  Summe unabhängiger Pro-Blatt-Bewertungen statt eines boolesche-Struktur-
  bewussten `ts_rank` - konsistent mit ADR 0012s bereits akzeptierter
  "nicht BM25-Niveau"-Einschränkung.
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


# --- Pfad 1: eine einzige to_tsquery-Zeichenkette -------------------------


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
    """ "a"/"b" gelten als Treffer, wenn sie in EINER der beiden Reihenfolgen
    innerhalb von `distance` Wörtern zueinander vorkommen - Postgres' `<N>`-
    Operator selbst verlangt genau N Wörter Abstand UND eine feste
    Reihenfolge, daher die ODER-Kette über 1..distance je Richtung."""
    variants = [f"{word_a}<{d}>{word_b}" for d in range(1, distance + 1)]
    variants += [f"{word_b}<{d}>{word_a}" for d in range(1, distance + 1)]
    return "(" + " | ".join(variants) + ")"


# --- Pfad 2: blattweise SQL-Boolesche Ausdrücke (enthält Fuzzy) -----------


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
        # Ein negiertes Blatt soll die Relevanz nicht beeinflussen.
        return literal(0.0)
    raise AssertionError(f"Unbekannter Knotentyp: {node!r}")
