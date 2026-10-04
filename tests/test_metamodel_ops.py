"""Tests for metamodel CRUD operations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.metamodel import (
    FieldSpec,
    InventorySpec,
    RelationSpec,
    table_name_for,
)
from rag_coding_mcp.db.metamodel_ops import (
    ConflictError,
    DependencyError,
    NotFoundError,
    ValidationError,
    add_field,
    create_inventory,
    create_relation_type,
    remove_field,
    remove_relation_type,
)
from rag_coding_mcp.db.seed import ADMIN_USER_ID, new_id


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "meta.db")
    initialize(c)
    return c


def _insert_minimal_node(
    conn: sqlite3.Connection, inventory: str, name: str = "example"
) -> str:
    nid = new_id()
    table = table_name_for(inventory)
    conn.execute(
        f"INSERT INTO {table} (id, name, owned_by, updated_by) VALUES (?, ?, ?, ?)",
        (nid, name, ADMIN_USER_ID, ADMIN_USER_ID),
    )
    return nid


# --- create_inventory ------------------------------------------------------


def test_create_inventory_adds_table_and_metamodel_rows(conn: sqlite3.Connection) -> None:
    spec = InventorySpec(
        name="Note",
        description="a sticky note",
        fields=(FieldSpec(name="body", type="text"),),
    )
    create_inventory(conn, spec)

    assert conn.execute(
        "SELECT 1 FROM metamodel_inventories WHERE name='Note'"
    ).fetchone() is not None
    row = conn.execute(
        "SELECT type FROM metamodel_fields WHERE inventory='Note' AND name='body'"
    ).fetchone()
    assert row["type"] == "text"

    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table_name_for('Note')})")}
    assert "body" in cols
    assert "owned_by" in cols


def test_create_inventory_rejects_duplicate(conn: sqlite3.Connection) -> None:
    spec = InventorySpec(name="Note", description="", fields=())
    create_inventory(conn, spec)
    with pytest.raises(ConflictError):
        create_inventory(conn, spec)


def test_create_inventory_rejects_bad_identifier(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError):
        create_inventory(conn, InventorySpec(name="1Bad", description="", fields=()))


def test_create_inventory_rejects_unknown_field_type(conn: sqlite3.Connection) -> None:
    bad = InventorySpec(
        name="Thing",
        description="",
        fields=(FieldSpec(name="x", type="nope"),),  # type: ignore[arg-type]
    )
    with pytest.raises(ValidationError):
        create_inventory(conn, bad)


def test_create_inventory_rejects_enum_without_values(conn: sqlite3.Connection) -> None:
    bad = InventorySpec(
        name="Thing",
        description="",
        fields=(FieldSpec(name="k", type="enum"),),
    )
    with pytest.raises(ValidationError):
        create_inventory(conn, bad)


def test_create_inventory_rejects_duplicate_fields(conn: sqlite3.Connection) -> None:
    bad = InventorySpec(
        name="Thing",
        description="",
        fields=(FieldSpec(name="x", type="string"), FieldSpec(name="x", type="int")),
    )
    with pytest.raises(ValidationError):
        create_inventory(conn, bad)


def test_create_inventory_rejects_field_colliding_with_base(conn: sqlite3.Connection) -> None:
    bad = InventorySpec(
        name="Thing",
        description="",
        fields=(FieldSpec(name="owned_by", type="string"),),
    )
    with pytest.raises(ValidationError):
        create_inventory(conn, bad)


# --- add_field -------------------------------------------------------------


def test_add_field_appends_column(conn: sqlite3.Connection) -> None:
    create_inventory(
        conn, InventorySpec(name="Note", description="", fields=())
    )
    add_field(conn, "Note", FieldSpec(name="body", type="text"))
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table_name_for('Note')})")}
    assert "body" in cols


def test_add_field_preserves_existing_rows(conn: sqlite3.Connection) -> None:
    create_inventory(conn, InventorySpec(name="Note", description="", fields=()))
    nid = _insert_minimal_node(conn, "Note")
    add_field(conn, "Note", FieldSpec(name="body", type="text"))
    row = conn.execute(
        f"SELECT id, body FROM {table_name_for('Note')} WHERE id=?", (nid,)
    ).fetchone()
    assert row["id"] == nid
    assert row["body"] is None


def test_add_field_rejects_duplicate(conn: sqlite3.Connection) -> None:
    with pytest.raises(ConflictError):
        add_field(conn, "File", FieldSpec(name="path", type="string"))


def test_add_field_rejects_required_when_rows_exist(conn: sqlite3.Connection) -> None:
    create_inventory(conn, InventorySpec(name="Note", description="", fields=()))
    _insert_minimal_node(conn, "Note")
    with pytest.raises(ValidationError):
        add_field(conn, "Note", FieldSpec(name="body", type="text", required=True))


def test_add_field_unknown_inventory(conn: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        add_field(conn, "DoesNotExist", FieldSpec(name="x", type="int"))


# --- remove_field ----------------------------------------------------------


def test_remove_field_removes_column_and_metamodel(conn: sqlite3.Connection) -> None:
    create_inventory(conn, InventorySpec(name="Note", description="", fields=()))
    add_field(conn, "Note", FieldSpec(name="body", type="text"))
    remove_field(conn, "Note", "body")
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table_name_for('Note')})")}
    assert "body" not in cols
    assert conn.execute(
        "SELECT 1 FROM metamodel_fields WHERE inventory='Note' AND name='body'"
    ).fetchone() is None


def test_remove_field_refuses_base_column(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError):
        remove_field(conn, "File", "owned_by")


def test_remove_field_refuses_when_data_present(conn: sqlite3.Connection) -> None:
    create_inventory(conn, InventorySpec(name="Note", description="", fields=()))
    add_field(conn, "Note", FieldSpec(name="body", type="text"))
    table = table_name_for("Note")
    conn.execute(
        f"INSERT INTO {table} (id, name, owned_by, updated_by, body) VALUES (?, ?, ?, ?, ?)",
        (new_id(), "n", ADMIN_USER_ID, ADMIN_USER_ID, "hello"),
    )
    with pytest.raises(DependencyError):
        remove_field(conn, "Note", "body")


def test_remove_field_unknown(conn: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        remove_field(conn, "File", "nope")
    with pytest.raises(NotFoundError):
        remove_field(conn, "Nope", "x")


# --- create_relation_type --------------------------------------------------


def test_create_relation_type(conn: sqlite3.Connection) -> None:
    create_inventory(conn, InventorySpec(name="Note", description="", fields=()))
    create_relation_type(
        conn, RelationSpec(name="annotates", from_inventory="Note", to_inventory="File")
    )
    assert conn.execute(
        "SELECT 1 FROM metamodel_relations WHERE name='annotates'"
    ).fetchone() is not None


def test_create_relation_type_rejects_duplicate(conn: sqlite3.Connection) -> None:
    with pytest.raises(ConflictError):
        # `contains` is seeded.
        create_relation_type(
            conn, RelationSpec(name="contains", from_inventory="Repo", to_inventory="File")
        )


def test_create_relation_type_rejects_bad_cardinality(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError):
        create_relation_type(
            conn,
            RelationSpec(
                name="weird", from_inventory="Repo", to_inventory="File", cardinality="lots"
            ),
        )


def test_create_relation_type_unknown_endpoint(conn: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        create_relation_type(
            conn, RelationSpec(name="x", from_inventory="Nope", to_inventory="File")
        )
    with pytest.raises(NotFoundError):
        create_relation_type(
            conn, RelationSpec(name="y", from_inventory="Repo", to_inventory="Nope")
        )


# --- remove_relation_type --------------------------------------------------


def test_remove_relation_type(conn: sqlite3.Connection) -> None:
    create_inventory(conn, InventorySpec(name="Note", description="", fields=()))
    create_relation_type(
        conn, RelationSpec(name="annotates", from_inventory="Note", to_inventory="File")
    )
    remove_relation_type(conn, "annotates")
    assert conn.execute(
        "SELECT 1 FROM metamodel_relations WHERE name='annotates'"
    ).fetchone() is None


def test_remove_relation_type_refuses_with_edges(conn: sqlite3.Connection) -> None:
    repo_id = _insert_minimal_node(conn, "Repo", "r1")
    file_id = new_id()
    conn.execute(
        f"INSERT INTO {table_name_for('File')}"
        " (id, name, owned_by, updated_by, path, content_hash)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (file_id, "f1", ADMIN_USER_ID, ADMIN_USER_ID, "a", "h"),
    )
    conn.execute(
        "INSERT INTO edges (from_id, relation, to_id) VALUES (?, 'contains', ?)",
        (repo_id, file_id),
    )
    with pytest.raises(DependencyError):
        remove_relation_type(conn, "contains")


def test_remove_relation_type_unknown(conn: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        remove_relation_type(conn, "nope")
