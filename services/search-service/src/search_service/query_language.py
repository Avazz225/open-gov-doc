"""Advanced full-text search query language (concept 3.7a, P14-S7).

Replaces the previous direct `websearch_to_tsquery(query)` (ADR 0012) with a
custom, small parser that additionally supports wildcards, fuzzy, and
proximity search - all on top of the already existing boolean/phrase
search, not an external search engine.

Syntax (modeled after common market reference products, concept 3.7a
literally "match up with common market reference products"):

    word1 word2        implied AND
    word1 OR word2      disjunction (case-insensitive)
    -word / NOT word    negation
    (...)               explicit parenthesization/precedence
    "exact phrase"      phrase search (exact word sequence)
    "word1 word2"~N     proximity search - exactly two words, matches if
                        they occur within N words of each other (either
                        order)
    word*               wildcard/prefix search
    word~ / word~N       fuzzy search, tolerance level N in {1,2,3} (default 2)
"""

from __future__ import annotations

from dataclasses import dataclass

FUZZY_LEVELS = (1, 2, 3)
DEFAULT_FUZZY_LEVEL = 2
# Trigram similarity thresholds per tolerance level (1 = strict/high
# similarity required, 3 = tolerant) - our own, documented mapping onto
# pg_trgm's 0..1 floating-point scale, not a Postgres convention.
FUZZY_THRESHOLDS = {1: 0.5, 2: 0.35, 3: 0.2}
# Upper bound on the word distance of a proximity search - prevents an
# arbitrarily large ~N from producing an unreasonably long OR chain of
# individual distances (see compile_query/_proximity_variants). The same
# kind of deliberate, documented limit as `search_result_hard_limit`.
MAX_PROXIMITY_DISTANCE = 20


class QuerySyntaxError(Exception):
    """For 400 responses in main.py - user-facing text, not an internal error."""


# --- AST ---------------------------------------------------------------


@dataclass(frozen=True)
class Term:
    word: str


@dataclass(frozen=True)
class Phrase:
    words: tuple[str, ...]


@dataclass(frozen=True)
class Prefix:
    word: str


@dataclass(frozen=True)
class Fuzzy:
    word: str
    level: int


@dataclass(frozen=True)
class Proximity:
    word_a: str
    word_b: str
    distance: int


@dataclass(frozen=True)
class And:
    left: Node
    right: Node


@dataclass(frozen=True)
class Or:
    left: Node
    right: Node


@dataclass(frozen=True)
class Not:
    child: Node


Node = Term | Phrase | Prefix | Fuzzy | Proximity | And | Or | Not


# --- Tokenizer -----------------------------------------------------------

_LPAREN = "LPAREN"
_RPAREN = "RPAREN"
_OR = "OR"
_NOT = "NOT"
_MINUS = "MINUS"
_WORD = "WORD"
_PREFIX = "PREFIX"
_FUZZY = "FUZZY"
_PHRASE = "PHRASE"


@dataclass(frozen=True)
class _Token:
    kind: str
    word: str | None = None
    words: tuple[str, ...] | None = None
    level: int | None = None
    distance: int | None = None


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def tokenize(raw: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n = 0, len(raw)
    # True right after line start/whitespace/'('/a keyword - only there may
    # a '-' be read as a negation prefix instead of as part of a
    # hyphenated word ("e-mail").
    at_term_start = True
    while i < n:
        ch = raw[i]
        if ch.isspace():
            i += 1
            at_term_start = True
            continue
        if ch == "(":
            tokens.append(_Token(_LPAREN))
            i += 1
            at_term_start = True
            continue
        if ch == ")":
            tokens.append(_Token(_RPAREN))
            i += 1
            at_term_start = False
            continue
        if ch == '"':
            j = raw.find('"', i + 1)
            if j == -1:
                raise QuerySyntaxError("Nicht geschlossenes Anführungszeichen in der Suchanfrage")
            words = tuple(raw[i + 1 : j].split())
            k = j + 1
            distance = None
            if k < n and raw[k] == "~":
                k += 1
                start = k
                while k < n and raw[k].isdigit():
                    k += 1
                if k == start:
                    raise QuerySyntaxError("Zahl nach '~' fehlt (Näherungssuche, z. B. \"a b\"~5)")
                distance = int(raw[start:k])
            if not words:
                raise QuerySyntaxError("Leere Phrase in Anführungszeichen")
            tokens.append(_Token(_PHRASE, words=words, distance=distance))
            i = k
            at_term_start = False
            continue
        if (
            ch == "-"
            and at_term_start
            and i + 1 < n
            and (_is_word_char(raw[i + 1]) or raw[i + 1] in '("')
        ):
            tokens.append(_Token(_MINUS))
            i += 1
            at_term_start = True
            continue
        if _is_word_char(ch):
            j = i
            while j < n and (_is_word_char(raw[j]) or raw[j] == "-"):
                j += 1
            word = raw[i:j]
            k = j
            if k < n and raw[k] == "*":
                tokens.append(_Token(_PREFIX, word=word))
                i = k + 1
                at_term_start = False
                continue
            if k < n and raw[k] == "~":
                k += 1
                start = k
                while k < n and raw[k].isdigit():
                    k += 1
                level = int(raw[start:k]) if k > start else None
                tokens.append(_Token(_FUZZY, word=word, level=level))
                i = k
                at_term_start = False
                continue
            upper = word.upper()
            if upper == "OR":
                tokens.append(_Token(_OR))
            elif upper == "NOT":
                tokens.append(_Token(_NOT))
            else:
                tokens.append(_Token(_WORD, word=word))
            i = k
            at_term_start = False
            continue
        # Unknown character (punctuation etc.) - permissively skip it, same
        # underlying stance as the previous websearch_to_tsquery, which
        # never reacts to "unexpected" input with an SQL error.
        i += 1
        at_term_start = False
    return tokens


# --- Parser (recursive descent, precedence: parens > NOT > AND > OR) -------


class _Cursor:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self) -> _Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok


def _parse_or(cur: _Cursor) -> Node:
    left = _parse_and(cur)
    while cur.peek() is not None and cur.peek().kind == _OR:
        cur.advance()
        right = _parse_and(cur)
        left = Or(left, right)
    return left


def _parse_and(cur: _Cursor) -> Node:
    left = _parse_unary(cur)
    while cur.peek() is not None and cur.peek().kind not in (_OR, _RPAREN):
        right = _parse_unary(cur)
        left = And(left, right)
    return left


def _parse_unary(cur: _Cursor) -> Node:
    tok = cur.peek()
    if tok is not None and tok.kind in (_MINUS, _NOT):
        cur.advance()
        return Not(_parse_unary(cur))
    return _parse_primary(cur)


def _parse_primary(cur: _Cursor) -> Node:
    tok = cur.peek()
    if tok is None:
        raise QuerySyntaxError("Unerwartetes Ende der Suchanfrage")
    if tok.kind == _LPAREN:
        cur.advance()
        node = _parse_or(cur)
        closing = cur.peek()
        if closing is None or closing.kind != _RPAREN:
            raise QuerySyntaxError("Fehlende schließende Klammer ')'")
        cur.advance()
        return node
    if tok.kind == _WORD:
        cur.advance()
        return Term(tok.word)
    if tok.kind == _PREFIX:
        cur.advance()
        return Prefix(tok.word)
    if tok.kind == _FUZZY:
        cur.advance()
        level = tok.level if tok.level is not None else DEFAULT_FUZZY_LEVEL
        if level not in FUZZY_LEVELS:
            raise QuerySyntaxError(
                f"Ungültige Fuzzy-Toleranzstufe '~{tok.level}' - erlaubt sind ~1/~2/~3"
            )
        return Fuzzy(tok.word, level)
    if tok.kind == _PHRASE:
        cur.advance()
        if tok.distance is None:
            return Phrase(tok.words)
        if len(tok.words) != 2:
            raise QuerySyntaxError(
                "Näherungssuche (~N) erfordert genau zwei Wörter in Anführungszeichen"
            )
        distance = max(1, min(tok.distance, MAX_PROXIMITY_DISTANCE))
        return Proximity(tok.words[0], tok.words[1], distance)
    raise QuerySyntaxError(f"Unerwartetes Zeichen an dieser Stelle der Suchanfrage: {tok.kind}")


def parse_query(raw: str | None) -> Node | None:
    """Returns `None` for an empty/whitespace-only query (= no full-text
    filtering, pure facet/attribute navigation, existing behavior
    unchanged)."""
    if raw is None or not raw.strip():
        return None
    tokens = tokenize(raw)
    if not tokens:
        return None
    cur = _Cursor(tokens)
    node = _parse_or(cur)
    if cur.peek() is not None:
        raise QuerySyntaxError(f"Unerwartetes '{cur.peek().kind}' in der Suchanfrage")
    return node


def contains_fuzzy(node: Node) -> bool:
    if isinstance(node, Fuzzy):
        return True
    if isinstance(node, (And, Or)):
        return contains_fuzzy(node.left) or contains_fuzzy(node.right)
    if isinstance(node, Not):
        return contains_fuzzy(node.child)
    return False
