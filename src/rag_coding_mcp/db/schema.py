"""Fixed support tables shared by every inventory.

Inventory-specific tables are created at runtime by `createInventory`
(see `rag_coding_mcp.db.metamodel`). This module only owns the invariant
tables that always exist.
"""

from __future__ import annotations

import sqlite3

SUPPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS metamodel_inventories (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS metamodel_fields (
    inventory        TEXT NOT NULL,
    name             TEXT NOT NULL,
    type             TEXT NOT NULL,
    enum_values_json TEXT,
    required         INTEGER NOT NULL DEFAULT 0,
    description      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (inventory, name),
    FOREIGN KEY (inventory) REFERENCES metamodel_inventories(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metamodel_relations (
    name            TEXT PRIMARY KEY,
    from_inventory  TEXT NOT NULL,
    to_inventory    TEXT NOT NULL,
    cardinality     TEXT NOT NULL DEFAULT 'many',
    description     TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (from_inventory) REFERENCES metamodel_inventories(name),
    FOREIGN KEY (to_inventory)   REFERENCES metamodel_inventories(name)
);

CREATE TABLE IF NOT EXISTS edges (
    from_id   TEXT NOT NULL,
    relation  TEXT NOT NULL,
    to_id     TEXT NOT NULL,
    PRIMARY KEY (from_id, relation, to_id),
    FOREIGN KEY (relation) REFERENCES metamodel_relations(name)
);

CREATE INDEX IF NOT EXISTS idx_edges_to       ON edges (to_id, relation);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges (relation);

CREATE TABLE IF NOT EXISTS embeddings (
    inventory    TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    model        TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    vector       BLOB NOT NULL,
    source_text  TEXT NOT NULL,
    PRIMARY KEY (inventory, node_id),
    FOREIGN KEY (inventory) REFERENCES metamodel_inventories(name)
);

CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
    inventory UNINDEXED,
    node_id   UNINDEXED,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def apply_support_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SUPPORT_SCHEMA)
