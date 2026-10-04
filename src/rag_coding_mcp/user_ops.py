"""Storage-level user CRUD. Users are not an inventory."""

from __future__ import annotations

import sqlite3

from rag_coding_mcp.db.seed import new_id


class UserError(Exception):
    code: str = "user_error"


class UserNotFound(UserError):
    code = "not_found"


class UserConflict(UserError):
    code = "conflict"


class UserValidationError(UserError):
    code = "validation_error"


def _check_name(name: str) -> None:
    if not name or not name.strip():
        raise UserValidationError("user name must be non-empty")


def list_users(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT id, name, is_admin, created_at FROM users ORDER BY created_at"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "isAdmin": bool(r["is_admin"]),
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


def create_user(conn: sqlite3.Connection, name: str, is_admin: bool = False) -> str:
    _check_name(name)
    uid = new_id()
    try:
        with conn:
            conn.execute(
                "INSERT INTO users (id, name, is_admin) VALUES (?, ?, ?)",
                (uid, name, 1 if is_admin else 0),
            )
    except sqlite3.IntegrityError as e:
        raise UserConflict(f"user name '{name}' already exists") from e
    return uid


def rename_user(conn: sqlite3.Connection, user_id: str, new_name: str) -> None:
    _check_name(new_name)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE users SET name = ? WHERE id = ?", (new_name, user_id)
            )
            if cur.rowcount == 0:
                raise UserNotFound(f"user {user_id} not found")
    except sqlite3.IntegrityError as e:
        raise UserConflict(f"user name '{new_name}' already exists") from e


def set_user_admin(conn: sqlite3.Connection, user_id: str, is_admin: bool) -> None:
    with conn:
        cur = conn.execute(
            "UPDATE users SET is_admin = ? WHERE id = ?",
            (1 if is_admin else 0, user_id),
        )
        if cur.rowcount == 0:
            raise UserNotFound(f"user {user_id} not found")


__all__ = [
    "UserConflict",
    "UserError",
    "UserNotFound",
    "UserValidationError",
    "create_user",
    "list_users",
    "rename_user",
    "set_user_admin",
]
