"""Tests for X-Actor-Id resolution."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rag_coding_mcp.actor import resolve_actor
from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.seed import ADMIN_USER_ID


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "actor.db")
    initialize(c)
    return c


def test_absent_header_resolves_to_admin(conn: sqlite3.Connection) -> None:
    actor = resolve_actor(conn, None)
    assert actor.id == ADMIN_USER_ID
    assert actor.is_admin is True
    assert actor.resolved_from_header is False


def test_blank_header_resolves_to_admin(conn: sqlite3.Connection) -> None:
    actor = resolve_actor(conn, "   ")
    assert actor.id == ADMIN_USER_ID
    assert actor.resolved_from_header is False


def test_unknown_header_resolves_to_admin(conn: sqlite3.Connection) -> None:
    actor = resolve_actor(conn, "nobody")
    assert actor.id == ADMIN_USER_ID
    assert actor.resolved_from_header is False


def test_known_header_resolves_to_that_user(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, name, is_admin) VALUES (?, ?, 0)",
        ("alice", "alice"),
    )
    actor = resolve_actor(conn, "alice")
    assert actor.id == "alice"
    assert actor.name == "alice"
    assert actor.is_admin is False
    assert actor.resolved_from_header is True


def test_header_value_is_trimmed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, name, is_admin) VALUES (?, ?, 0)",
        ("bob", "bob"),
    )
    actor = resolve_actor(conn, "  bob  ")
    assert actor.id == "bob"
    assert actor.resolved_from_header is True


def test_missing_admin_raises(tmp_path: Path) -> None:
    c = connect(tmp_path / "noadmin.db")
    from rag_coding_mcp.db.schema import apply_support_schema

    apply_support_schema(c)
    with pytest.raises(RuntimeError):
        resolve_actor(c, None)
