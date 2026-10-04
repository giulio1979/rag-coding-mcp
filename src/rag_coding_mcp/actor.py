"""Actor identity resolution.

Reads `X-Actor-Id` from the request context, resolves it against the `users`
table, and falls back to the seeded admin user when the header is absent or
names an unknown user.

This module is intentionally transport-agnostic: it operates on a sqlite3
Connection and a header value. The server glue (FastMCP, HTTP) extracts the
header and calls `resolve_actor`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rag_coding_mcp.db.seed import ADMIN_USER_ID

ACTOR_HEADER = "X-Actor-Id"


@dataclass(frozen=True)
class Actor:
    id: str
    name: str
    is_admin: bool
    resolved_from_header: bool


def resolve_actor(conn: sqlite3.Connection, header_value: str | None) -> Actor:
    """Return the actor to attribute writes to for this request.

    Fallback rules:
      - header absent / blank  → admin user
      - header set but unknown → admin user
      - header set and matches → that user
    """
    if header_value is not None and header_value.strip():
        row = conn.execute(
            "SELECT id, name, is_admin FROM users WHERE id = ?",
            (header_value.strip(),),
        ).fetchone()
        if row is not None:
            return Actor(
                id=row["id"],
                name=row["name"],
                is_admin=bool(row["is_admin"]),
                resolved_from_header=True,
            )

    row = conn.execute(
        "SELECT id, name, is_admin FROM users WHERE id = ?",
        (ADMIN_USER_ID,),
    ).fetchone()
    if row is None:
        raise RuntimeError("admin user missing; initialize() was not called")
    return Actor(
        id=row["id"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
        resolved_from_header=False,
    )
