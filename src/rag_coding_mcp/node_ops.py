"""Storage-level CRUD for inventory nodes.

Resolvers are thin wrappers over these. Keeping the SQL here means the
GraphQL layer never has to care about column-by-column plumbing.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from rag_coding_mcp.db.metamodel import BASE_COLUMNS, FieldSpec, InventorySpec, table_name_for
from rag_coding_mcp.db.seed import new_id
from rag_coding_mcp.embeddings import EmbedderProtocol
from rag_coding_mcp.node_writes import upsert_node_embedding
from rag_coding_mcp.search import delete_node_fts, upsert_node_fts


class NodeError(Exception):
    code: str = "node_error"


class NodeNotFound(NodeError):
    code = "not_found"


class NodeValidationError(NodeError):
    code = "validation_error"


_WRITABLE_BASE = ("name", "description")


def _validate_fields(spec: InventorySpec, values: dict[str, Any], *, creating: bool) -> None:
    allowed = {f.name for f in spec.fields}
    for key in values:
        if key not in allowed:
            raise NodeValidationError(f"unknown field '{key}' on {spec.name}")
    if creating:
        for f in spec.fields:
            if f.required and (f.name not in values or values[f.name] is None):
                raise NodeValidationError(f"missing required field '{f.name}' on {spec.name}")
    for f in spec.fields:
        if f.name not in values:
            continue
        v = values[f.name]
        if v is None:
            if f.required:
                raise NodeValidationError(f"field '{f.name}' cannot be null")
            continue
        if f.type == "enum" and f.enum_values and v not in f.enum_values:
            raise NodeValidationError(
                f"field '{f.name}' value {v!r} not in {list(f.enum_values)}"
            )


def create_node(
    conn: sqlite3.Connection,
    spec: InventorySpec,
    *,
    name: str,
    description: str,
    actor_id: str,
    fields: dict[str, Any] | None = None,
    embedder: EmbedderProtocol | None = None,
) -> str:
    fields = fields or {}
    _validate_fields(spec, fields, creating=True)
    node_id = new_id()
    columns = ["id", "name", "description", "owned_by", "updated_by"]
    values: list[Any] = [node_id, name, description, actor_id, actor_id]
    for fs in spec.fields:
        if fs.name in fields:
            columns.append(fs.name)
            values.append(fields[fs.name])
    placeholders = ",".join("?" for _ in values)
    with conn:
        conn.execute(
            f"INSERT INTO {table_name_for(spec.name)} ({','.join(columns)})"
            f" VALUES ({placeholders})",
            values,
        )
        upsert_node_fts(conn, spec.name, node_id, f"{name}\n{description}")
        if embedder is not None:
            upsert_node_embedding(conn, embedder, spec.name, node_id, name, description)
    return node_id


def update_node(
    conn: sqlite3.Connection,
    spec: InventorySpec,
    node_id: str,
    *,
    actor_id: str,
    name: str | None = None,
    description: str | None = None,
    fields: dict[str, Any] | None = None,
    embedder: EmbedderProtocol | None = None,
) -> None:
    fields = fields or {}
    _validate_fields(spec, fields, creating=False)
    table = table_name_for(spec.name)
    existing = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (node_id,)).fetchone()
    if existing is None:
        raise NodeNotFound(f"{spec.name} {node_id} not found")

    sets: list[str] = ["updated_by = ?", "updated_at = CURRENT_TIMESTAMP"]
    values: list[Any] = [actor_id]
    new_name = existing["name"] if name is None else name
    new_desc = existing["description"] if description is None else description
    if name is not None:
        sets.append("name = ?")
        values.append(name)
    if description is not None:
        sets.append("description = ?")
        values.append(description)
    for fs in spec.fields:
        if fs.name in fields:
            sets.append(f"{fs.name} = ?")
            values.append(fields[fs.name])
    values.append(node_id)
    with conn:
        conn.execute(f"UPDATE {table} SET {','.join(sets)} WHERE id = ?", values)
        if name is not None or description is not None:
            upsert_node_fts(conn, spec.name, node_id, f"{new_name}\n{new_desc}")
            if embedder is not None:
                upsert_node_embedding(conn, embedder, spec.name, node_id, new_name, new_desc)


def delete_node(conn: sqlite3.Connection, spec: InventorySpec, node_id: str) -> None:
    table = table_name_for(spec.name)
    out_rels = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM metamodel_relations WHERE from_inventory = ?", (spec.name,)
        )
    ]
    in_rels = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM metamodel_relations WHERE to_inventory = ?", (spec.name,)
        )
    ]
    with conn:
        cur = conn.execute(f"DELETE FROM {table} WHERE id = ?", (node_id,))
        if cur.rowcount == 0:
            raise NodeNotFound(f"{spec.name} {node_id} not found")
        delete_node_fts(conn, spec.name, node_id)
        conn.execute(
            "DELETE FROM embeddings WHERE inventory = ? AND node_id = ?",
            (spec.name, node_id),
        )
        if out_rels:
            placeholders = ",".join("?" for _ in out_rels)
            conn.execute(
                f"DELETE FROM edges WHERE from_id = ? AND relation IN ({placeholders})",
                [node_id, *out_rels],
            )
        if in_rels:
            placeholders = ",".join("?" for _ in in_rels)
            conn.execute(
                f"DELETE FROM edges WHERE to_id = ? AND relation IN ({placeholders})",
                [node_id, *in_rels],
            )


__all__ = [
    "BASE_COLUMNS",
    "FieldSpec",
    "NodeError",
    "NodeNotFound",
    "NodeValidationError",
    "create_node",
    "delete_node",
    "update_node",
]
