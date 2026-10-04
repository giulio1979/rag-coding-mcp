"""Tests for metamodel mutations and schema cache invalidation (tasks 27, 29)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from graphql import graphql_sync, print_schema

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.seed import ADMIN_USER_ID
from rag_coding_mcp.graphql import SchemaCache, SchemaContext


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "mm.db")
    initialize(c)
    return c


def _run(cache: SchemaCache, conn: sqlite3.Connection, query: str, **vars: object):
    ctx = SchemaContext(
        conn=conn,
        embedder=None,
        actor_id=ADMIN_USER_ID,
        on_metamodel_change=cache.invalidate,
    )
    return graphql_sync(
        cache.get(), query, variable_values=dict(vars) or None, context_value=ctx
    )


def test_cache_initially_builds_once(conn: sqlite3.Connection) -> None:
    cache = SchemaCache(conn)
    s1 = cache.get()
    s2 = cache.get()
    assert s1 is s2
    assert cache.version == 0


def test_create_inventory_invalidates_and_shows_new_type(conn: sqlite3.Connection) -> None:
    cache = SchemaCache(conn)
    before = cache.get()
    assert "type Widget" not in print_schema(before)

    result = _run(
        cache,
        conn,
        'mutation { createInventory(name: "Widget", description: "a widget") }',
    )
    assert result.errors is None, result.errors
    assert result.data["createInventory"] is True
    assert cache.version == 1

    rebuilt = cache.get()
    assert rebuilt is not before
    sdl = print_schema(rebuilt)
    assert "type Widget" in sdl
    assert "createWidget" in sdl
    assert "widgets(" in sdl


def test_add_field_shows_up_on_next_request(conn: sqlite3.Connection) -> None:
    cache = SchemaCache(conn)
    _run(cache, conn, 'mutation { createInventory(name: "Note") }')
    result = _run(
        cache,
        conn,
        'mutation { addField(inventory: "Note", field: { name: "tag", type: "string" }) }',
    )
    assert result.errors is None, result.errors
    sdl = print_schema(cache.get())
    assert "tag: String" in sdl


def test_remove_field_disappears_from_schema(conn: sqlite3.Connection) -> None:
    cache = SchemaCache(conn)
    _run(cache, conn, 'mutation { createInventory(name: "Note") }')
    _run(
        cache,
        conn,
        'mutation { addField(inventory: "Note", field: { name: "tag", type: "string" }) }',
    )
    assert "tag: String" in print_schema(cache.get())
    _run(cache, conn, 'mutation { removeField(inventory: "Note", name: "tag") }')
    assert "tag: String" not in print_schema(cache.get())


def test_create_and_remove_relation_type(conn: sqlite3.Connection) -> None:
    cache = SchemaCache(conn)
    _run(cache, conn, 'mutation { createInventory(name: "A") }')
    _run(cache, conn, 'mutation { createInventory(name: "B") }')
    r = _run(
        cache,
        conn,
        'mutation { createRelationType(name: "links_to",'
        ' fromInventory: "A", toInventory: "B") }',
    )
    assert r.errors is None, r.errors
    sdl = print_schema(cache.get())
    assert "linksTo: [B!]!" in sdl
    assert "linksToIn: [A!]!" in sdl

    r2 = _run(cache, conn, 'mutation { removeRelationType(name: "links_to") }')
    assert r2.errors is None, r2.errors
    assert "linksTo: [B!]!" not in print_schema(cache.get())


def test_metamodel_mutation_errors_do_not_invalidate(conn: sqlite3.Connection) -> None:
    cache = SchemaCache(conn)
    cache.get()
    base_version = cache.version
    r = _run(
        cache,
        conn,
        'mutation { createInventory(name: "Repo") }',
    )
    assert r.errors is not None
    assert cache.version == base_version
