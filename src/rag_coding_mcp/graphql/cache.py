"""Caches a built GraphQLSchema and rebuilds it on demand.

The schema is derived from the metamodel at runtime, so metamodel mutations
must invalidate the cache. Keep this trivial — no locking, no TTL: Python's
GIL plus SQLite's transaction model is enough for the single-process server.
"""

from __future__ import annotations

import sqlite3

from graphql import GraphQLSchema

from rag_coding_mcp.graphql.schema import build_schema


class SchemaCache:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._schema: GraphQLSchema | None = None
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    def get(self) -> GraphQLSchema:
        if self._schema is None:
            self._schema = build_schema(self._conn)
        return self._schema

    def invalidate(self) -> None:
        self._schema = None
        self._version += 1


__all__ = ["SchemaCache"]
