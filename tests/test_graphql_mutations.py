"""Tests for GraphQL mutations and relation traversal (task 26)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from graphql import graphql_sync, print_schema

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.metamodel import table_name_for
from rag_coding_mcp.db.seed import ADMIN_USER_ID
from rag_coding_mcp.graphql import SchemaContext, build_schema


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "m.db")
    initialize(c)
    return c


def _ctx(conn: sqlite3.Connection, actor_id: str = ADMIN_USER_ID) -> SchemaContext:
    return SchemaContext(conn=conn, embedder=None, actor_id=actor_id)


# --- SDL shape ------------------------------------------------------------


def test_sdl_contains_mutation_and_traversals(conn: sqlite3.Connection) -> None:
    sdl = print_schema(build_schema(conn))
    assert "type Mutation" in sdl
    assert "createRepo(input: CreateRepoInput!): Repo!" in sdl
    assert "updateFile(id: ID!, input: UpdateFileInput!): File!" in sdl
    assert "deleteSOP(id: ID!): Boolean!" in sdl
    assert "connect(relation: String!, fromId: ID!, toId: ID!): Boolean!" in sdl
    # Traversal fields: Repo.contains -> [File!]!, File.containsIn -> [Repo!]!
    assert "contains: [File!]!" in sdl
    assert "containsIn: [Repo!]!" in sdl


# --- create / update / delete attribution --------------------------------


def test_create_sets_owner_from_actor(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    conn.execute(
        "INSERT INTO users (id, name, is_admin) VALUES (?, ?, 0)", ("alice", "alice")
    )
    result = graphql_sync(
        schema,
        'mutation { createRepo(input: { name: "repo1" }) { id ownedBy updatedBy } }',
        context_value=_ctx(conn, actor_id="alice"),
    )
    assert result.errors is None, result.errors
    r = result.data["createRepo"]
    assert r["ownedBy"] == "alice"
    assert r["updatedBy"] == "alice"


def test_update_preserves_owner_changes_updater(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    conn.execute(
        "INSERT INTO users (id, name, is_admin) VALUES (?, ?, 0)", ("alice", "alice")
    )
    created = graphql_sync(
        schema,
        'mutation { createRepo(input: { name: "repo1" }) { id } }',
        context_value=_ctx(conn, actor_id="alice"),
    )
    assert created.errors is None, created.errors
    rid = created.data["createRepo"]["id"]

    result = graphql_sync(
        schema,
        'mutation($id: ID!) { updateRepo(id: $id, input: { description: "new" })'
        " { ownedBy updatedBy description } }",
        variable_values={"id": rid},
        context_value=_ctx(conn, actor_id=ADMIN_USER_ID),
    )
    assert result.errors is None, result.errors
    assert result.data["updateRepo"] == {
        "ownedBy": "alice",
        "updatedBy": ADMIN_USER_ID,
        "description": "new",
    }


def test_delete_returns_true(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    created = graphql_sync(
        schema,
        'mutation { createRepo(input: { name: "r" }) { id } }',
        context_value=_ctx(conn),
    )
    rid = created.data["createRepo"]["id"]
    result = graphql_sync(
        schema,
        "mutation($id: ID!) { deleteRepo(id: $id) }",
        variable_values={"id": rid},
        context_value=_ctx(conn),
    )
    assert result.errors is None, result.errors
    assert result.data["deleteRepo"] is True
    assert conn.execute(f"SELECT COUNT(*) c FROM {table_name_for('Repo')}").fetchone()["c"] == 0


def test_create_file_requires_fields(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        'mutation { createFile(input: { name: "f" }) { id } }',
        context_value=_ctx(conn),
    )
    assert result.errors is not None


# --- connect / disconnect + traversal ------------------------------------


def test_connect_and_traverse(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    r = graphql_sync(
        schema,
        'mutation { createRepo(input: { name: "r" }) { id } }',
        context_value=_ctx(conn),
    )
    assert r.errors is None, r.errors
    f = graphql_sync(
        schema,
        'mutation { createFile(input: { name: "f", path: "a", contentHash: "h" }) { id } }',
        context_value=_ctx(conn),
    )
    # File input uses declared field names so this ought to fail — contentHash
    # is not a declared field name; declared is content_hash.
    assert f.errors is not None


def test_connect_and_traverse_correct_fields(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    r = graphql_sync(
        schema,
        'mutation { createRepo(input: { name: "r" }) { id } }',
        context_value=_ctx(conn),
    )
    rid = r.data["createRepo"]["id"]
    f = graphql_sync(
        schema,
        'mutation { createFile(input: { name: "f", path: "a", content_hash: "h" }) { id } }',
        context_value=_ctx(conn),
    )
    assert f.errors is None, f.errors
    fid = f.data["createFile"]["id"]

    conn_result = graphql_sync(
        schema,
        'mutation($r: ID!, $f: ID!) { connect(relation: "contains", fromId: $r, toId: $f) }',
        variable_values={"r": rid, "f": fid},
        context_value=_ctx(conn),
    )
    assert conn_result.errors is None, conn_result.errors
    assert conn_result.data["connect"] is True

    traversal = graphql_sync(
        schema,
        "query($id: ID!) { repo(id: $id) { contains { id name } } }",
        variable_values={"id": rid},
        context_value=_ctx(conn),
    )
    assert traversal.errors is None, traversal.errors
    assert traversal.data["repo"]["contains"] == [{"id": fid, "name": "f"}]

    reverse = graphql_sync(
        schema,
        "query($id: ID!) { file(id: $id) { containsIn { id name } } }",
        variable_values={"id": fid},
        context_value=_ctx(conn),
    )
    assert reverse.errors is None, reverse.errors
    assert reverse.data["file"]["containsIn"] == [{"id": rid, "name": "r"}]


def test_disconnect(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    r = graphql_sync(
        schema,
        'mutation { createRepo(input: { name: "r" }) { id } }',
        context_value=_ctx(conn),
    )
    rid = r.data["createRepo"]["id"]
    f = graphql_sync(
        schema,
        'mutation { createFile(input: { name: "f", path: "a", content_hash: "h" }) { id } }',
        context_value=_ctx(conn),
    )
    fid = f.data["createFile"]["id"]
    graphql_sync(
        schema,
        'mutation($r: ID!, $f: ID!) { connect(relation: "contains", fromId: $r, toId: $f) }',
        variable_values={"r": rid, "f": fid},
        context_value=_ctx(conn),
    )
    disc = graphql_sync(
        schema,
        'mutation($r: ID!, $f: ID!) { disconnect(relation: "contains", fromId: $r, toId: $f) }',
        variable_values={"r": rid, "f": fid},
        context_value=_ctx(conn),
    )
    assert disc.errors is None, disc.errors
    assert disc.data["disconnect"] is True
