"""Tests for storage-level node and edge CRUD (task 26 scaffolding)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.metamodel import InventorySpec, table_name_for
from rag_coding_mcp.db.metamodel_read import read_metamodel
from rag_coding_mcp.db.seed import ADMIN_USER_ID
from rag_coding_mcp.edge_ops import (
    EdgeNotFound,
    EdgeValidationError,
    UnknownRelation,
    connect_edge,
    disconnect_edge,
    list_incoming,
    list_outgoing,
)
from rag_coding_mcp.node_ops import (
    NodeNotFound,
    NodeValidationError,
    create_node,
    delete_node,
    update_node,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "n.db")
    initialize(c)
    return c


def _spec(conn: sqlite3.Connection, name: str) -> InventorySpec:
    inventories, _ = read_metamodel(conn)
    for inv in inventories:
        if inv.name == name:
            return inv
    raise AssertionError(f"inventory {name} missing")


# --- node CRUD ------------------------------------------------------------


def test_create_sets_attribution(conn: sqlite3.Connection) -> None:
    repo = _spec(conn, "Repo")
    nid = create_node(conn, repo, name="r", description="d", actor_id=ADMIN_USER_ID)
    row = conn.execute(f"SELECT * FROM {table_name_for('Repo')} WHERE id = ?", (nid,)).fetchone()
    assert row["owned_by"] == ADMIN_USER_ID
    assert row["updated_by"] == ADMIN_USER_ID


def test_create_requires_required_fields(conn: sqlite3.Connection) -> None:
    file_spec = _spec(conn, "File")
    with pytest.raises(NodeValidationError):
        create_node(conn, file_spec, name="x", description="", actor_id=ADMIN_USER_ID)


def test_create_rejects_bad_enum(conn: sqlite3.Connection) -> None:
    sop = _spec(conn, "SOP")
    with pytest.raises(NodeValidationError):
        create_node(
            conn, sop, name="x", description="", actor_id=ADMIN_USER_ID,
            fields={"category": "nonsense", "body": "b"},
        )


def test_update_preserves_owner_changes_updater(conn: sqlite3.Connection) -> None:
    repo = _spec(conn, "Repo")
    nid = create_node(conn, repo, name="r", description="d", actor_id=ADMIN_USER_ID)
    conn.execute(
        "INSERT INTO users (id, name, is_admin) VALUES (?, ?, 0)", ("alice", "alice")
    )
    update_node(conn, repo, nid, actor_id="alice", description="new")
    row = conn.execute(f"SELECT * FROM {table_name_for('Repo')} WHERE id = ?", (nid,)).fetchone()
    assert row["owned_by"] == ADMIN_USER_ID
    assert row["updated_by"] == "alice"
    assert row["description"] == "new"


def test_update_rejects_unknown_field(conn: sqlite3.Connection) -> None:
    repo = _spec(conn, "Repo")
    nid = create_node(conn, repo, name="r", description="", actor_id=ADMIN_USER_ID)
    with pytest.raises(NodeValidationError):
        update_node(conn, repo, nid, actor_id=ADMIN_USER_ID, fields={"ghost": 1})


def test_update_missing(conn: sqlite3.Connection) -> None:
    repo = _spec(conn, "Repo")
    with pytest.raises(NodeNotFound):
        update_node(conn, repo, "nope", actor_id=ADMIN_USER_ID, name="x")


def test_delete_removes_node_fts_and_edges(conn: sqlite3.Connection) -> None:
    repo = _spec(conn, "Repo")
    file_spec = _spec(conn, "File")
    repo_id = create_node(conn, repo, name="r", description="", actor_id=ADMIN_USER_ID)
    file_id = create_node(
        conn, file_spec, name="f", description="", actor_id=ADMIN_USER_ID,
        fields={"path": "a", "content_hash": "h"},
    )
    connect_edge(conn, "contains", repo_id, file_id, actor_id=ADMIN_USER_ID)
    delete_node(conn, repo, repo_id)
    assert conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM node_fts WHERE node_id = ?", (repo_id,)).fetchone()["c"] == 0


def test_delete_missing(conn: sqlite3.Connection) -> None:
    repo = _spec(conn, "Repo")
    with pytest.raises(NodeNotFound):
        delete_node(conn, repo, "nope")


# --- edges ----------------------------------------------------------------


def _mk_repo_file(conn: sqlite3.Connection) -> tuple[str, str]:
    repo = _spec(conn, "Repo")
    file_spec = _spec(conn, "File")
    repo_id = create_node(conn, repo, name="r", description="", actor_id=ADMIN_USER_ID)
    file_id = create_node(
        conn, file_spec, name="f", description="", actor_id=ADMIN_USER_ID,
        fields={"path": "a", "content_hash": "h"},
    )
    return repo_id, file_id


def test_connect_and_list(conn: sqlite3.Connection) -> None:
    repo_id, file_id = _mk_repo_file(conn)
    connect_edge(conn, "contains", repo_id, file_id, actor_id=ADMIN_USER_ID)
    assert list_outgoing(conn, "contains", repo_id) == [file_id]
    assert list_incoming(conn, "contains", file_id) == [repo_id]


def test_connect_is_idempotent(conn: sqlite3.Connection) -> None:
    repo_id, file_id = _mk_repo_file(conn)
    connect_edge(conn, "contains", repo_id, file_id, actor_id=ADMIN_USER_ID)
    connect_edge(conn, "contains", repo_id, file_id, actor_id=ADMIN_USER_ID)
    assert len(list_outgoing(conn, "contains", repo_id)) == 1


def test_connect_rejects_unknown_relation(conn: sqlite3.Connection) -> None:
    repo_id, file_id = _mk_repo_file(conn)
    with pytest.raises(UnknownRelation):
        connect_edge(conn, "bogus", repo_id, file_id, actor_id=ADMIN_USER_ID)


def test_connect_rejects_missing_nodes(conn: sqlite3.Connection) -> None:
    repo_id, _ = _mk_repo_file(conn)
    with pytest.raises(EdgeValidationError):
        connect_edge(conn, "contains", repo_id, "ghost", actor_id=ADMIN_USER_ID)


def test_disconnect(conn: sqlite3.Connection) -> None:
    repo_id, file_id = _mk_repo_file(conn)
    connect_edge(conn, "contains", repo_id, file_id, actor_id=ADMIN_USER_ID)
    disconnect_edge(conn, "contains", repo_id, file_id)
    assert list_outgoing(conn, "contains", repo_id) == []


def test_disconnect_missing(conn: sqlite3.Connection) -> None:
    repo_id, file_id = _mk_repo_file(conn)
    with pytest.raises(EdgeNotFound):
        disconnect_edge(conn, "contains", repo_id, file_id)
