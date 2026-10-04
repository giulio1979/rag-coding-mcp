"""Full metamodel CRUD operations with validation and structured errors.

These functions wrap the low-level helpers in `db.metamodel` with input
validation, uniqueness checks, data-dependency guards and single transactions.
They are the ones the GraphQL layer will call.
"""

from __future__ import annotations

import re
import sqlite3
from typing import get_args

from rag_coding_mcp.db.metamodel import (
    BASE_COLUMNS,
    FieldSpec,
    FieldType,
    InventorySpec,
    RelationSpec,
    create_inventory_table,
    create_relation,
    table_name_for,
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_FIELD_TYPES: frozenset[str] = frozenset(get_args(FieldType))
_CARDINALITIES: frozenset[str] = frozenset({"one", "many"})


class MetamodelError(Exception):
    """Base class for metamodel validation and conflict errors."""

    code: str = "metamodel_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(MetamodelError):
    code = "validation_error"


class ConflictError(MetamodelError):
    code = "conflict"


class NotFoundError(MetamodelError):
    code = "not_found"


class DependencyError(MetamodelError):
    code = "dependency_exists"


def _check_identifier(kind: str, value: str) -> None:
    if not _IDENTIFIER_RE.match(value):
        raise ValidationError(f"{kind} {value!r} must match [A-Za-z][A-Za-z0-9_]*")


def _check_field_spec(f: FieldSpec) -> None:
    _check_identifier("field name", f.name)
    if f.name in BASE_COLUMNS:
        raise ValidationError(f"field {f.name!r} collides with a base column")
    if f.type not in _FIELD_TYPES:
        raise ValidationError(f"field {f.name!r} has unknown type {f.type!r}")
    if f.type == "enum":
        if not f.enum_values:
            raise ValidationError(f"enum field {f.name!r} requires enum_values")
        if len(set(f.enum_values)) != len(f.enum_values):
            raise ValidationError(f"enum field {f.name!r} has duplicate values")
    elif f.enum_values:
        raise ValidationError(f"non-enum field {f.name!r} must not set enum_values")


def _inventory_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM metamodel_inventories WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def _relation_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM metamodel_relations WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def _field_exists(conn: sqlite3.Connection, inventory: str, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM metamodel_fields WHERE inventory = ? AND name = ?",
        (inventory, name),
    ).fetchone()
    return row is not None


def create_inventory(conn: sqlite3.Connection, spec: InventorySpec) -> None:
    """Create a new inventory: validation, table DDL, metamodel rows, in one transaction."""
    _check_identifier("inventory name", spec.name)
    seen: set[str] = set()
    for f in spec.fields:
        if f.name in seen:
            raise ValidationError(f"duplicate field name {f.name!r}")
        seen.add(f.name)
        _check_field_spec(f)

    if _inventory_exists(conn, spec.name):
        raise ConflictError(f"inventory {spec.name!r} already exists")

    with conn:
        create_inventory_table(conn, spec)


def add_field(conn: sqlite3.Connection, inventory: str, field: FieldSpec) -> None:
    """Add a column to an inventory's table and record it in the metamodel."""
    _check_field_spec(field)
    if not _inventory_exists(conn, inventory):
        raise NotFoundError(f"inventory {inventory!r} does not exist")
    if _field_exists(conn, inventory, field.name):
        raise ConflictError(
            f"field {field.name!r} already exists on inventory {inventory!r}"
        )
    if field.required:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table_name_for(inventory)}"
        ).fetchone()
        if row["c"] > 0:
            raise ValidationError(
                f"cannot add required field {field.name!r}: inventory {inventory!r}"
                " already contains rows"
            )

    sqlite_type = {
        "string": "TEXT",
        "text": "TEXT",
        "enum": "TEXT",
        "int": "INTEGER",
        "bool": "INTEGER",
    }[field.type]
    not_null = " NOT NULL" if field.required else ""
    column_sql = f"{field.name} {sqlite_type}{not_null}"

    import json as _json

    with conn:
        conn.execute(
            f"ALTER TABLE {table_name_for(inventory)} ADD COLUMN {column_sql}"
        )
        conn.execute(
            """
            INSERT INTO metamodel_fields
                (inventory, name, type, enum_values_json, required, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                inventory,
                field.name,
                field.type,
                _json.dumps(list(field.enum_values)) if field.enum_values else None,
                1 if field.required else 0,
                field.description,
            ),
        )


def remove_field(conn: sqlite3.Connection, inventory: str, name: str) -> None:
    """Drop a field from an inventory.

    Refuses when:
      - the field is a base column,
      - the inventory does not exist,
      - the field does not exist,
      - any row has a non-NULL value in that column.
    """
    if name in BASE_COLUMNS:
        raise ValidationError(f"cannot remove base column {name!r}")
    if not _inventory_exists(conn, inventory):
        raise NotFoundError(f"inventory {inventory!r} does not exist")
    if not _field_exists(conn, inventory, name):
        raise NotFoundError(f"field {name!r} does not exist on {inventory!r}")

    table = table_name_for(inventory)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE {name} IS NOT NULL"
    ).fetchone()
    if row["c"] > 0:
        raise DependencyError(
            f"cannot remove field {name!r} on {inventory!r}: {row['c']} row(s)"
            " have a value"
        )

    with conn:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
        conn.execute(
            "DELETE FROM metamodel_fields WHERE inventory = ? AND name = ?",
            (inventory, name),
        )


def create_relation_type(conn: sqlite3.Connection, spec: RelationSpec) -> None:
    """Create a relation type between two existing inventories."""
    _check_identifier("relation name", spec.name)
    if spec.cardinality not in _CARDINALITIES:
        raise ValidationError(
            f"cardinality must be one of {sorted(_CARDINALITIES)}, got {spec.cardinality!r}"
        )
    if _relation_exists(conn, spec.name):
        raise ConflictError(f"relation {spec.name!r} already exists")
    if not _inventory_exists(conn, spec.from_inventory):
        raise NotFoundError(f"from_inventory {spec.from_inventory!r} does not exist")
    if not _inventory_exists(conn, spec.to_inventory):
        raise NotFoundError(f"to_inventory {spec.to_inventory!r} does not exist")

    with conn:
        create_relation(conn, spec)


def remove_relation_type(conn: sqlite3.Connection, name: str) -> None:
    """Remove a relation type. Refuses when any edges still use it."""
    if not _relation_exists(conn, name):
        raise NotFoundError(f"relation {name!r} does not exist")
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM edges WHERE relation = ?", (name,)
    ).fetchone()
    if row["c"] > 0:
        raise DependencyError(
            f"cannot remove relation {name!r}: {row['c']} edge(s) still reference it"
        )

    with conn:
        conn.execute("DELETE FROM metamodel_relations WHERE name = ?", (name,))
