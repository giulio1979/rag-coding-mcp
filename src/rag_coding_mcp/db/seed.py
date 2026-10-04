"""Idempotent startup seeding: admin user and built-in metamodel."""

from __future__ import annotations

import sqlite3
import uuid

from rag_coding_mcp.db.metamodel import (
    FieldSpec,
    InventorySpec,
    RelationSpec,
    create_inventory_table,
    create_relation,
)

ADMIN_USER_ID = "admin"
ADMIN_USER_NAME = "admin"


def seed_admin_user(conn: sqlite3.Connection) -> None:
    """Insert the default admin user if the users table is empty."""
    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if count > 0:
        return
    conn.execute(
        "INSERT INTO users (id, name, is_admin) VALUES (?, ?, 1)",
        (ADMIN_USER_ID, ADMIN_USER_NAME),
    )


BUILTIN_INVENTORIES: tuple[InventorySpec, ...] = (
    InventorySpec(
        name="Repo",
        description="A repository tracked by the knowledge server.",
        fields=(),
    ),
    InventorySpec(
        name="File",
        description="A file within a repository (summary and metadata, never contents).",
        fields=(
            FieldSpec(name="path", type="string", required=True),
            FieldSpec(name="content_hash", type="string", required=True),
        ),
    ),
    InventorySpec(
        name="Method",
        description="A function, method, class or module defined within a file.",
        fields=(
            FieldSpec(name="signature", type="string"),
            FieldSpec(
                name="kind",
                type="enum",
                enum_values=("function", "method", "class", "module"),
                required=True,
            ),
        ),
    ),
    InventorySpec(
        name="SOP",
        description="A Standard Operating Procedure document for a repo.",
        fields=(
            FieldSpec(
                name="category",
                type="enum",
                enum_values=("testing", "deploy", "building", "coding", "running"),
                required=True,
            ),
            FieldSpec(name="body", type="text", required=True),
        ),
    ),
)

BUILTIN_RELATIONS: tuple[RelationSpec, ...] = (
    RelationSpec(name="contains", from_inventory="Repo", to_inventory="File"),
    RelationSpec(name="defines", from_inventory="File", to_inventory="Method"),
    RelationSpec(
        name="calls", from_inventory="Method", to_inventory="Method", cardinality="many"
    ),
    RelationSpec(name="has_sop", from_inventory="Repo", to_inventory="SOP"),
    RelationSpec(name="references_file", from_inventory="SOP", to_inventory="File"),
    RelationSpec(name="references_method", from_inventory="SOP", to_inventory="Method"),
)


def seed_builtin_metamodel(conn: sqlite3.Connection) -> None:
    """Create the built-in inventories, their tables, and the built-in relations.

    Idempotent: if `metamodel_inventories` is non-empty we assume seeding already
    happened and only replay the relation inserts (which use OR IGNORE) so that
    adding a built-in relation in a future version does not require a migration.
    """
    inventory_count = conn.execute(
        "SELECT COUNT(*) AS c FROM metamodel_inventories"
    ).fetchone()["c"]

    if inventory_count == 0:
        for spec in BUILTIN_INVENTORIES:
            create_inventory_table(conn, spec)

    for rel in BUILTIN_RELATIONS:
        create_relation(conn, rel)


def new_id() -> str:
    """UUID4 as a plain hex string. Used by writers once nodes land."""
    return uuid.uuid4().hex
