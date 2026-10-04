"""Tests for identifier splitting, FTS writes, vector scan, and RRF."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.metamodel import InventorySpec, table_name_for
from rag_coding_mcp.db.metamodel_ops import create_inventory
from rag_coding_mcp.db.seed import ADMIN_USER_ID, new_id
from rag_coding_mcp.node_writes import upsert_node_embedding
from rag_coding_mcp.search import (
    FusedResult,
    delete_node_fts,
    fts_search,
    reciprocal_rank_fusion,
    split_identifier,
    tokenize_for_fts,
    upsert_node_fts,
    vector_search,
)
from tests.test_embeddings import _FakeEmbedder  # reuse the deterministic embedder

# --- split_identifier ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("getUserName", ["get", "user", "name"]),
        ("get_user_name", ["get", "user", "name"]),
        ("HTTPServer", ["http", "server"]),
        ("parseURL2Response", ["parse", "url", "2", "response"]),
        ("my-kebab-case", ["my", "kebab", "case"]),
        ("SCREAMING_SNAKE", ["screaming", "snake"]),
        ("plain words here", ["plain", "words", "here"]),
        ("", []),
    ],
)
def test_split_identifier(text: str, expected: list[str]) -> None:
    assert split_identifier(text) == expected


def test_tokenize_includes_original_and_parts() -> None:
    out = tokenize_for_fts("getUserName")
    assert "getUserName" in out
    for p in ("get", "user", "name"):
        assert p in out


# --- FTS round-trip --------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "search.db")
    initialize(c)
    create_inventory(c, InventorySpec(name="Note", description="", fields=()))
    return c


def _insert_note(conn: sqlite3.Connection, name: str, description: str = "") -> str:
    nid = new_id()
    conn.execute(
        f"INSERT INTO {table_name_for('Note')}"
        " (id, name, description, owned_by, updated_by) VALUES (?, ?, ?, ?, ?)",
        (nid, name, description, ADMIN_USER_ID, ADMIN_USER_ID),
    )
    upsert_node_fts(conn, "Note", nid, f"{name}\n{description}")
    return nid


def test_fts_matches_exact_name(conn: sqlite3.Connection) -> None:
    nid = _insert_note(conn, "getUserName", "fetches the user")
    results = fts_search(conn, "Note", "getUserName")
    assert nid in [r[0] for r in results]


def test_fts_matches_across_case_styles(conn: sqlite3.Connection) -> None:
    nid_camel = _insert_note(conn, "getUserName", "")
    nid_snake = _insert_note(conn, "get_user_name", "")

    found = {r[0] for r in fts_search(conn, "Note", "getUserName")}
    assert nid_camel in found
    assert nid_snake in found

    found = {r[0] for r in fts_search(conn, "Note", "get_user_name")}
    assert nid_camel in found
    assert nid_snake in found


def test_fts_delete(conn: sqlite3.Connection) -> None:
    nid = _insert_note(conn, "widget", "")
    assert fts_search(conn, "Note", "widget")
    delete_node_fts(conn, "Note", nid)
    assert not fts_search(conn, "Note", "widget")


def test_fts_scope_is_inventory(conn: sqlite3.Connection) -> None:
    # Insert into Note; search a different inventory and get nothing.
    _insert_note(conn, "unique_token_abc", "")
    assert not fts_search(conn, "Repo", "unique_token_abc")


def test_fts_empty_query_returns_empty(conn: sqlite3.Connection) -> None:
    _insert_note(conn, "x", "")
    assert fts_search(conn, "Note", "") == []
    assert fts_search(conn, "Note", "   ") == []


# --- vector search ---------------------------------------------------------


def test_vector_search_orders_by_cosine(conn: sqlite3.Connection) -> None:
    emb = _FakeEmbedder()
    nid1 = _insert_note(conn, "alpha", "the first greek letter")
    nid2 = _insert_note(conn, "beta", "the second greek letter")
    upsert_node_embedding(conn, emb, "Note", nid1, "alpha", "the first greek letter")
    upsert_node_embedding(conn, emb, "Note", nid2, "beta", "the second greek letter")

    results = vector_search(conn, emb, "Note", "alpha\nthe first greek letter")
    assert results, "expected non-empty results"
    assert results[0][0] == nid1


def test_vector_search_empty_query(conn: sqlite3.Connection) -> None:
    emb = _FakeEmbedder()
    assert vector_search(conn, emb, "Note", "") == []


def test_vector_search_ignores_other_models(conn: sqlite3.Connection) -> None:
    old = _FakeEmbedder(model_name="old")
    new = _FakeEmbedder(model_name="new")
    nid = _insert_note(conn, "alpha", "x")
    upsert_node_embedding(conn, old, "Note", nid, "alpha", "x")
    results = vector_search(conn, new, "Note", "alpha")
    assert results == []


# --- reciprocal rank fusion ------------------------------------------------


def test_rrf_lexical_only_tags_lexical() -> None:
    out = reciprocal_rank_fusion([("a", 1.0), ("b", 0.5)], [])
    kinds = {r.node_id: r.matched_by for r in out}
    assert kinds == {"a": "LEXICAL", "b": "LEXICAL"}


def test_rrf_semantic_only_tags_semantic() -> None:
    out = reciprocal_rank_fusion([], [("a", 0.9)])
    assert out[0].matched_by == "SEMANTIC"


def test_rrf_overlap_tags_both_and_wins() -> None:
    lexical = [("a", 1.0), ("b", 0.5)]
    semantic = [("a", 0.9), ("c", 0.5)]
    out = reciprocal_rank_fusion(lexical, semantic)
    by_id = {r.node_id: r for r in out}
    assert by_id["a"].matched_by == "BOTH"
    assert by_id["b"].matched_by == "LEXICAL"
    assert by_id["c"].matched_by == "SEMANTIC"
    assert out[0].node_id == "a"


def test_rrf_respects_limit() -> None:
    lexical = [(f"n{i}", 1.0) for i in range(10)]
    out = reciprocal_rank_fusion(lexical, [], limit=3)
    assert len(out) == 3
    assert all(isinstance(r, FusedResult) for r in out)
