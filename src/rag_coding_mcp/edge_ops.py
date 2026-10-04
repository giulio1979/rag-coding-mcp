"""Storage-level edge operations: connect / disconnect / list by relation."""

from __future__ import annotations

import sqlite3

from rag_coding_mcp.db.metamodel import RelationSpec, table_name_for


class EdgeError(Exception):
    code: str = "edge_error"


class EdgeValidationError(EdgeError):
    code = "validation_error"


class EdgeNotFound(EdgeError):
    code = "not_found"


class UnknownRelation(EdgeError):
    code = "unknown_relation"


def _load_relation(conn: sqlite3.Connection, name: str) -> RelationSpec:
    row = conn.execute(
        "SELECT name, from_inventory, to_inventory, cardinality, description"
        " FROM metamodel_relations WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        raise UnknownRelation(f"relation '{name}' not defined")
    return RelationSpec(
        name=row["name"],
        from_inventory=row["from_inventory"],
        to_inventory=row["to_inventory"],
        cardinality=row["cardinality"],
        description=row["description"],
    )


def _node_exists(conn: sqlite3.Connection, inventory: str, node_id: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {table_name_for(inventory)} WHERE id = ?", (node_id,)
    ).fetchone()
    return row is not None


def connect_edge(
    conn: sqlite3.Connection,
    relation: str,
    from_id: str,
    to_id: str,
    *,
    actor_id: str,  # noqa: ARG001 - reserved for future attribution column
) -> None:
    spec = _load_relation(conn, relation)
    if not _node_exists(conn, spec.from_inventory, from_id):
        raise EdgeValidationError(f"{spec.from_inventory} {from_id} does not exist")
    if not _node_exists(conn, spec.to_inventory, to_id):
        raise EdgeValidationError(f"{spec.to_inventory} {to_id} does not exist")

    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO edges (from_id, relation, to_id) VALUES (?, ?, ?)",
            (from_id, relation, to_id),
        )


def disconnect_edge(
    conn: sqlite3.Connection, relation: str, from_id: str, to_id: str
) -> None:
    _load_relation(conn, relation)
    with conn:
        cur = conn.execute(
            "DELETE FROM edges WHERE from_id = ? AND relation = ? AND to_id = ?",
            (from_id, relation, to_id),
        )
        if cur.rowcount == 0:
            raise EdgeNotFound(f"no edge '{relation}' from {from_id} to {to_id}")


def list_outgoing(conn: sqlite3.Connection, relation: str, from_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT to_id FROM edges WHERE relation = ? AND from_id = ? ORDER BY to_id",
        (relation, from_id),
    ).fetchall()
    return [r["to_id"] for r in rows]


def list_incoming(conn: sqlite3.Connection, relation: str, to_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT from_id FROM edges WHERE relation = ? AND to_id = ? ORDER BY from_id",
        (relation, to_id),
    ).fetchall()
    return [r["from_id"] for r in rows]


__all__ = [
    "EdgeError",
    "EdgeNotFound",
    "EdgeValidationError",
    "UnknownRelation",
    "connect_edge",
    "disconnect_edge",
    "list_incoming",
    "list_outgoing",
]
