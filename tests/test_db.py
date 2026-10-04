"""Tests for db.connect, support schema, and seeding."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.metamodel import BASE_COLUMNS, table_name_for
from rag_coding_mcp.db.seed import (
    ADMIN_USER_ID,
    BUILTIN_INVENTORIES,
    BUILTIN_RELATIONS,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _initialized_conn(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    initialize(conn)
    return conn


def test_connect_sets_wal_mode(db_path: Path) -> None:
    conn = connect(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_connect_enables_foreign_keys(db_path: Path) -> None:
    conn = connect(db_path)
    on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert on == 1


def test_initialize_creates_support_tables(db_path: Path) -> None:
    conn = _initialized_conn(db_path)
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "users",
        "metamodel_inventories",
        "metamodel_fields",
        "metamodel_relations",
        "edges",
        "embeddings",
    } <= tables


def test_initialize_creates_fts_virtual_table(db_path: Path) -> None:
    conn = _initialized_conn(db_path)
    rows = list(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name='node_fts'"
        )
    )
    assert rows, "node_fts virtual table should exist"


def test_initialize_seeds_admin_user(db_path: Path) -> None:
    conn = _initialized_conn(db_path)
    row = conn.execute("SELECT id, name, is_admin FROM users").fetchone()
    assert row is not None
    assert row["id"] == ADMIN_USER_ID
    assert row["name"] == "admin"
    assert row["is_admin"] == 1


def test_initialize_seeds_builtin_inventories(db_path: Path) -> None:
    conn = _initialized_conn(db_path)
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM metamodel_inventories")
    }
    assert names == {spec.name for spec in BUILTIN_INVENTORIES}


def test_initialize_seeds_builtin_relations(db_path: Path) -> None:
    conn = _initialized_conn(db_path)
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM metamodel_relations")
    }
    assert names == {rel.name for rel in BUILTIN_RELATIONS}


def test_each_inventory_table_has_base_columns(db_path: Path) -> None:
    conn = _initialized_conn(db_path)
    for spec in BUILTIN_INVENTORIES:
        table = table_name_for(spec.name)
        cols = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for base in BASE_COLUMNS:
            assert base in cols, f"{table} missing base column {base}"


def test_inventory_specific_fields_present(db_path: Path) -> None:
    conn = _initialized_conn(db_path)
    file_cols = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name_for('File')})")
    }
    assert {"path", "content_hash"} <= file_cols

    method_cols = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name_for('Method')})")
    }
    assert {"signature", "kind"} <= method_cols

    sop_cols = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name_for('SOP')})")
    }
    assert {"category", "body"} <= sop_cols


def test_initialize_is_idempotent(db_path: Path) -> None:
    conn = _initialized_conn(db_path)
    initialize(conn)
    initialize(conn)

    assert conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 1
    assert (
        conn.execute("SELECT COUNT(*) AS c FROM metamodel_inventories").fetchone()["c"]
        == len(BUILTIN_INVENTORIES)
    )
    assert (
        conn.execute("SELECT COUNT(*) AS c FROM metamodel_relations").fetchone()["c"]
        == len(BUILTIN_RELATIONS)
    )


def test_second_connect_sees_prior_state(db_path: Path) -> None:
    conn1 = _initialized_conn(db_path)
    conn1.close()
    conn2 = connect(db_path)
    initialize(conn2)
    row = conn2.execute("SELECT id FROM users WHERE id=?", (ADMIN_USER_ID,)).fetchone()
    assert row is not None


def test_field_name_colliding_with_base_column_rejected() -> None:
    from rag_coding_mcp.db.metamodel import (
        FieldSpec,
        InventorySpec,
        create_inventory_table,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from rag_coding_mcp.db.schema import apply_support_schema

    apply_support_schema(conn)
    bad = InventorySpec(
        name="Bad",
        description="",
        fields=(FieldSpec(name="name", type="string"),),
    )
    with pytest.raises(ValueError):
        create_inventory_table(conn, bad)
