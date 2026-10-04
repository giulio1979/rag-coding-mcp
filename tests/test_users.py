"""Tests for the user management slice (task 28)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from graphql import graphql_sync, print_schema

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.seed import ADMIN_USER_ID
from rag_coding_mcp.graphql import SchemaContext, build_schema


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "u.db")
    initialize(c)
    return c


def _ctx(conn: sqlite3.Connection) -> SchemaContext:
    return SchemaContext(conn=conn, embedder=None, actor_id=ADMIN_USER_ID)


def test_user_type_in_sdl_but_not_in_metamodel(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    sdl = print_schema(schema)
    assert "type User" in sdl
    assert "users: [User!]!" in sdl
    # Users are not an inventory and must not surface in the metamodel listing.
    result = graphql_sync(
        schema, "{ metamodel { inventories { name } } }", context_value=_ctx(conn)
    )
    names = {inv["name"] for inv in result.data["metamodel"]["inventories"]}
    assert "User" not in names


def test_users_query_returns_admin(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    result = graphql_sync(
        schema, "{ users { id name isAdmin } }", context_value=_ctx(conn)
    )
    assert result.errors is None, result.errors
    assert result.data["users"] == [{"id": ADMIN_USER_ID, "name": "admin", "isAdmin": True}]


def test_create_user(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    result = graphql_sync(
        schema,
        'mutation { createUser(name: "alice") { id name isAdmin } }',
        context_value=_ctx(conn),
    )
    assert result.errors is None, result.errors
    assert result.data["createUser"]["name"] == "alice"
    assert result.data["createUser"]["isAdmin"] is False


def test_create_user_duplicate(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    graphql_sync(
        schema, 'mutation { createUser(name: "alice") { id } }', context_value=_ctx(conn)
    )
    result = graphql_sync(
        schema, 'mutation { createUser(name: "alice") { id } }', context_value=_ctx(conn)
    )
    assert result.errors is not None


def test_rename_user(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    created = graphql_sync(
        schema, 'mutation { createUser(name: "alice") { id } }', context_value=_ctx(conn)
    )
    uid = created.data["createUser"]["id"]
    result = graphql_sync(
        schema,
        'mutation($id: ID!) { renameUser(id: $id, name: "ally") { name } }',
        variable_values={"id": uid},
        context_value=_ctx(conn),
    )
    assert result.errors is None, result.errors
    assert result.data["renameUser"]["name"] == "ally"


def test_set_user_admin(conn: sqlite3.Connection) -> None:
    schema = build_schema(conn)
    created = graphql_sync(
        schema, 'mutation { createUser(name: "alice") { id } }', context_value=_ctx(conn)
    )
    uid = created.data["createUser"]["id"]
    result = graphql_sync(
        schema,
        "mutation($id: ID!) { setUserAdmin(id: $id, isAdmin: true) { isAdmin } }",
        variable_values={"id": uid},
        context_value=_ctx(conn),
    )
    assert result.errors is None, result.errors
    assert result.data["setUserAdmin"]["isAdmin"] is True
