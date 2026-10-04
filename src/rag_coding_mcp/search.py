"""Search primitives: identifier splitter, FTS5 writers, vector scan, RRF.

Kept in one module because the pieces are small and share domain language
with each other. Larger moves split out later if needed.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from rag_coding_mcp.embeddings import EmbedderProtocol, decode_vector

# --- identifier splitter ---------------------------------------------------

_WORD_SPLIT_RE = re.compile(
    r"""
    [A-Z]+(?=[A-Z][a-z])   # acronym followed by a word, e.g. HTTP in HTTPServer
    | [A-Z]?[a-z]+          # Capitalised word or lowercase run
    | [A-Z]+                # trailing acronym
    | \d+                   # standalone digit run
    """,
    re.VERBOSE,
)


def split_identifier(text: str) -> list[str]:
    """Split an identifier into lower-cased word parts.

    Handles camelCase, PascalCase, snake_case, kebab-case, SCREAMING_SNAKE,
    acronyms, and embedded digits. Non-identifier text passes through as its
    own tokens (also split on whitespace and punctuation).
    """
    out: list[str] = []
    for chunk in re.split(r"[\s_\-./]+", text):
        if not chunk:
            continue
        parts = _WORD_SPLIT_RE.findall(chunk)
        for p in parts:
            out.append(p.lower())
    return out


def tokenize_for_fts(text: str) -> str:
    """Produce an FTS5-friendly string: original text + split identifier tokens.

    Keeping the original text lets exact-phrase matches work; the appended
    split tokens let `getUserName` match a stored `get_user_name`.
    """
    tokens = split_identifier(text)
    if not tokens:
        return text
    return text + " " + " ".join(tokens)


# --- FTS5 writers ----------------------------------------------------------


def upsert_node_fts(
    conn: sqlite3.Connection, inventory: str, node_id: str, source_text: str
) -> None:
    """Replace a node's FTS row with the current source text (plus split tokens)."""
    delete_node_fts(conn, inventory, node_id)
    conn.execute(
        "INSERT INTO node_fts (inventory, node_id, text) VALUES (?, ?, ?)",
        (inventory, node_id, tokenize_for_fts(source_text)),
    )


def delete_node_fts(conn: sqlite3.Connection, inventory: str, node_id: str) -> None:
    conn.execute(
        "DELETE FROM node_fts WHERE inventory = ? AND node_id = ?",
        (inventory, node_id),
    )


def fts_search(
    conn: sqlite3.Connection, inventory: str, query: str, limit: int = 50
) -> list[tuple[str, float]]:
    """Return (node_id, rank) pairs for lexical matches, best first.

    FTS5's `rank` is negative (lower is better). We convert to a positive score
    where higher is better so the fusion code can treat it uniformly.
    """
    if not query.strip():
        return []
    rows = conn.execute(
        """
        SELECT node_id, rank
          FROM node_fts
         WHERE inventory = ? AND node_fts MATCH ?
         ORDER BY rank
         LIMIT ?
        """,
        (inventory, _escape_fts_query(query), limit),
    ).fetchall()
    return [(r["node_id"], -float(r["rank"])) for r in rows]


def _escape_fts_query(query: str) -> str:
    """Rewrite the query so split identifier tokens participate too.

    Users type `getUserName`; we also want to search for the parts. We treat
    the input as a free-text FTS MATCH over the original + split tokens.
    """
    tokens = split_identifier(query)
    if not tokens:
        return query
    quoted = [f'"{t}"' for t in {*tokens, query.strip()} if t]
    return " OR ".join(quoted)


# --- vector search ---------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def vector_search(
    conn: sqlite3.Connection,
    embedder: EmbedderProtocol,
    inventory: str,
    query: str,
    limit: int = 50,
) -> list[tuple[str, float]]:
    """Return (node_id, cosine) pairs, best first, within an inventory."""
    if not query.strip():
        return []
    query_vector = decode_vector(embedder.embed([query])[0])
    rows = conn.execute(
        """
        SELECT node_id, vector
          FROM embeddings
         WHERE inventory = ? AND model = ?
        """,
        (inventory, embedder.model_name),
    ).fetchall()
    scored = [(r["node_id"], _cosine(query_vector, decode_vector(r["vector"]))) for r in rows]
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:limit]


# --- reciprocal rank fusion ------------------------------------------------


MatchedBy = Literal["LEXICAL", "SEMANTIC", "BOTH"]


@dataclass(frozen=True)
class FusedResult:
    node_id: str
    score: float
    matched_by: MatchedBy


def reciprocal_rank_fusion(
    lexical: list[tuple[str, float]],
    semantic: list[tuple[str, float]],
    k: int = 60,
    limit: int = 50,
) -> list[FusedResult]:
    """Merge two ranked lists, tagging each result with its match sources."""
    scores: dict[str, float] = {}
    sources: dict[str, set[str]] = {}

    for rank, (node_id, _) in enumerate(lexical):
        scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank + 1)
        sources.setdefault(node_id, set()).add("LEXICAL")

    for rank, (node_id, _) in enumerate(semantic):
        scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank + 1)
        sources.setdefault(node_id, set()).add("SEMANTIC")

    merged = sorted(scores.items(), key=lambda t: t[1], reverse=True)[:limit]
    out: list[FusedResult] = []
    for node_id, score in merged:
        src = sources[node_id]
        matched_by: MatchedBy = (
            "BOTH" if len(src) == 2 else ("LEXICAL" if "LEXICAL" in src else "SEMANTIC")
        )
        out.append(FusedResult(node_id=node_id, score=score, matched_by=matched_by))
    return out
