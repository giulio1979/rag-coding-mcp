"""Metamodel storage operations.

Owns the mapping from metamodel entries to actual SQLite tables for inventories,
plus a canonical list of base columns that every inventory table must have.

Only the subset needed for seeding and startup is implemented here. The full
`createInventory` / `addField` / `removeField` surface (with validation and
rebuilds) lands in later tasks.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Literal

FieldType = Literal["string", "int", "bool", "enum", "text"]

BASE_COLUMNS_SQL = """
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    owned_by    TEXT NOT NULL REFERENCES users(id),
    updated_by  TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
"""

BASE_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "description",
    "owned_by",
    "updated_by",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: FieldType
    required: bool = False
    enum_values: tuple[str, ...] | None = None
    description: str = ""


@dataclass(frozen=True)
class InventorySpec:
    name: str
    description: str
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class RelationSpec:
    name: str
    from_inventory: str
    to_inventory: str
    cardinality: str = "many"
    description: str = ""


def _column_sql(field: FieldSpec) -> str:
    sqlite_type = {
        "string": "TEXT",
        "text": "TEXT",
        "enum": "TEXT",
        "int": "INTEGER",
        "bool": "INTEGER",
    }[field.type]
    not_null = " NOT NULL" if field.required else ""
    return f"{field.name} {sqlite_type}{not_null}"


def table_name_for(inventory: str) -> str:
    """Resolve the SQLite table name for an inventory.

    Inventory names are PascalCase in the metamodel; tables are snake_cased
    with an `inv_` prefix to make them trivially recognisable in a backup.
    """
    out = []
    for i, ch in enumerate(inventory):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "inv_" + "".join(out)


def create_inventory_table(conn: sqlite3.Connection, spec: InventorySpec) -> None:
    """Create the SQLite table backing an inventory and record its metamodel rows.

    This helper is intentionally minimal: it assumes the caller has already
    verified that the inventory does not yet exist. Returns silently when the
    table already exists (idempotent for startup seeding).
    """
    for base_name in BASE_COLUMNS:
        for f in spec.fields:
            if f.name == base_name:
                raise ValueError(f"field {f.name!r} collides with a base column")

    extra_columns = ",\n    ".join(_column_sql(f) for f in spec.fields)
    columns_sql = BASE_COLUMNS_SQL + (",\n    " + extra_columns if extra_columns else "")
    table = table_name_for(spec.name)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n)")

    conn.execute(
        "INSERT OR IGNORE INTO metamodel_inventories(name, description) VALUES (?, ?)",
        (spec.name, spec.description),
    )
    for f in spec.fields:
        conn.execute(
            """
            INSERT OR IGNORE INTO metamodel_fields
                (inventory, name, type, enum_values_json, required, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                spec.name,
                f.name,
                f.type,
                json.dumps(list(f.enum_values)) if f.enum_values else None,
                1 if f.required else 0,
                f.description,
            ),
        )


def create_relation(conn: sqlite3.Connection, spec: RelationSpec) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO metamodel_relations
            (name, from_inventory, to_inventory, cardinality, description)
        VALUES (?, ?, ?, ?, ?)
        """,
        (spec.name, spec.from_inventory, spec.to_inventory, spec.cardinality, spec.description),
    )
