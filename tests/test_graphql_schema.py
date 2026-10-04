"""Tests for the dynamic GraphQL schema (tasks 24 and 25)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from graphql import graphql_sync

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.metamodel import table_name_for
from rag_coding_mcp.db.seed import ADMIN_USER_ID, new_id
from rag_coding_mcp.graphql import SchemaContext, build_schema, print_sdl
from rag_coding_mcp.node_writes import upsert_node_embedding
from rag_coding_mcp.search import upsert_node_fts
from tests.test_embeddings import _FakeEmbedder


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "gql.db")
    initialize(c)
    return c


def _ctx(conn: sqlite3.Connection, embedder=None) -> SchemaContext:
    return SchemaContext(conn=conn, embedder=embedder, actor_id=ADMIN_USER_ID)


def _insert_sop(conn: sqlite3.Connection, name: str, body: str, category: str = "coding") -> str:
    nid = new_id()
    conn.execute(
        f"INSERT INTO {table_name_for('SOP')}"
        " (id, name, description, owned_by, updated_by, category, body)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (nid, name, "", ADMIN_USER_ID, ADMIN_USER_ID, category, body),
    )
    upsert_node_fts(conn, "SOP", nid, f"{name}\n{body}")
    return nid


# --- schema shape ---------------------------------------------------------


def test_sdl_contains_builtin_inventories(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    sdl = print_sdl(schema)
    for inv in ("Repo", "File", "Method", "SOP"):
        assert f"type {inv} " in sdl, inv
        assert f"type {inv}Result " in sdl, inv


def test_sdl_includes_matched_by_enum(conn: sqlite3.Connection) -> None:
    sdl = print_sdl(build_schema(conn))
    assert "enum MatchedBy" in sdl
    for v in ("LEXICAL", "SEMANTIC", "BOTH"):
        assert v in sdl


def test_sdl_includes_list_and_single_queries(conn: sqlite3.Connection) -> None:
    sdl = print_sdl(build_schema(conn))
    assert "sop(id: ID!): SOP" in sdl
    assert "sops(" in sdl


# --- metamodel introspection ----------------------------------------------


def test_metamodel_query_lists_inventories(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        "{ metamodel { inventories { name fields { name type required } } } }",
        context_value=_ctx(conn),
    )
    assert result.errors is None, result.errors
    names = {inv["name"] for inv in result.data["metamodel"]["inventories"]}
    assert {"Repo", "File", "Method", "SOP"} <= names


def test_metamodel_query_returns_sdl_and_embedding_info(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        "{ metamodel { sdl embeddingModel embeddingDim } }",
        context_value=_ctx(conn, embedder=_FakeEmbedder()),
    )
    assert result.errors is None, result.errors
    assert "type Query" in result.data["metamodel"]["sdl"]
    assert result.data["metamodel"]["embeddingModel"] == "fake-v1"
    assert result.data["metamodel"]["embeddingDim"] == 8


# --- single lookup --------------------------------------------------------


def test_single_lookup(conn: sqlite3.Connection) -> None:
    nid = _insert_sop(conn, "deploy", "do stuff", category="deploy")
    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        "query($id: ID!) { sop(id: $id) { id name category body } }",
        variable_values={"id": nid},
        context_value=_ctx(conn),
    )
    assert result.errors is None, result.errors
    assert result.data["sop"] == {"id": nid, "name": "deploy", "category": "deploy", "body": "do stuff"}


def test_single_lookup_missing(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        "{ sop(id: \"nope\") { id } }",
        context_value=_ctx(conn),
    )
    assert result.errors is None
    assert result.data["sop"] is None


def test_multiword_field_round_trips_as_declared(conn: sqlite3.Connection) -> None:
    """Custom inventory fields keep their declared name in both read paths.

    Regression: `_row_to_node` used to camelCase every column, which
    produced a `content_hash` GraphQL field whose resolver returned
    `None` because the row dict was keyed `contentHash`.
    """
    schema = build_schema(conn)
    created = graphql_sync(
        schema,
        'mutation { createFile(input: { name: "f", path: "p", content_hash: "deadbeef" }) { id content_hash path } }',
        context_value=_ctx(conn),
    )
    assert created.errors is None, created.errors
    assert created.data["createFile"]["content_hash"] == "deadbeef"

    listed = graphql_sync(
        schema,
        "{ files { node { content_hash path } } }",
        context_value=_ctx(conn),
    )
    assert listed.errors is None, listed.errors
    assert listed.data["files"][0]["node"]["content_hash"] == "deadbeef"


# --- list query / hybrid search -------------------------------------------


def test_list_without_filters_returns_all(conn: sqlite3.Connection) -> None:
    _insert_sop(conn, "one", "")
    _insert_sop(conn, "two", "")
    schema = build_schema(conn)
    result = graphql_sync(
        schema, "{ sops { node { name } matchedBy } }", context_value=_ctx(conn)
    )
    assert result.errors is None, result.errors
    names = sorted(r["node"]["name"] for r in result.data["sops"])
    assert names == ["one", "two"]


def test_list_text_search(conn: sqlite3.Connection) -> None:
    _insert_sop(conn, "deployProduction", "")
    _insert_sop(conn, "runTests", "")
    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        '{ sops(text: "deploy_production") { node { name } matchedBy } }',
        context_value=_ctx(conn),
    )
    assert result.errors is None, result.errors
    hits = [r["node"]["name"] for r in result.data["sops"]]
    assert "deployProduction" in hits
    assert all(r["matchedBy"] == "LEXICAL" for r in result.data["sops"])


def test_list_similar_to_search(conn: sqlite3.Connection) -> None:
    emb = _FakeEmbedder()
    nid_a = _insert_sop(conn, "alpha", "")
    nid_b = _insert_sop(conn, "beta", "")
    upsert_node_embedding(conn, emb, "SOP", nid_a, "alpha", "")
    upsert_node_embedding(conn, emb, "SOP", nid_b, "beta", "")

    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        '{ sops(similarTo: "alpha\\n") { node { name } matchedBy } }',
        context_value=_ctx(conn, embedder=emb),
    )
    assert result.errors is None, result.errors
    assert result.data["sops"][0]["node"]["name"] == "alpha"
    assert result.data["sops"][0]["matchedBy"] == "SEMANTIC"


def test_list_hybrid_tags_both(conn: sqlite3.Connection) -> None:
    emb = _FakeEmbedder()
    nid = _insert_sop(conn, "deployAlpha", "")
    upsert_node_embedding(conn, emb, "SOP", nid, "deployAlpha", "")
    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        '{ sops(text: "deploy", similarTo: "deployAlpha\\n") { node { name } matchedBy } }',
        context_value=_ctx(conn, embedder=emb),
    )
    assert result.errors is None, result.errors
    assert result.data["sops"][0]["matchedBy"] == "BOTH"


def test_similar_to_without_embedder_errors(conn: sqlite3.Connection) -> None:
    _insert_sop(conn, "x", "")
    schema = build_schema(conn)
    result = graphql_sync(
        schema, '{ sops(similarTo: "x") { node { name } } }', context_value=_ctx(conn)
    )
    assert result.errors is not None
    assert "embedder" in str(result.errors[0]).lower()
