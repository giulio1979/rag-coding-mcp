"""SQLite connection and startup initialization.

`connect(path)` opens a connection in WAL mode, applies the fixed support schema,
and seeds the built-in metamodel on an empty database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rag_coding_mcp.db.schema import apply_support_schema
from rag_coding_mcp.db.seed import seed_admin_user, seed_builtin_metamodel


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection ready for use by the server.

    `check_same_thread=False` because FastMCP and Starlette run handlers in
    a worker threadpool. SQLite serializes writes internally; concurrent
    reads are safe in WAL mode.
    """
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Idempotently apply the fixed schema and seed defaults."""
    apply_support_schema(conn)
    seed_admin_user(conn)
    seed_builtin_metamodel(conn)
