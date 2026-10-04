"""Tests for embedding packing, upsert and stale re-embedding.

Uses a deterministic fake embedder so pytest does not pull models from HF.
A single real-model test guards against regressions in the fastembed path;
mark it `slow` so it can be opted out when iterating.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.metamodel import InventorySpec, table_name_for
from rag_coding_mcp.db.metamodel_ops import create_inventory
from rag_coding_mcp.db.seed import ADMIN_USER_ID, new_id
from rag_coding_mcp.embeddings import (
    DEFAULT_DIM,
    build_source_text,
    decode_vector,
    encode_vector,
)
from rag_coding_mcp.node_writes import (
    iter_stale_embeddings,
    reembed_stale,
    upsert_node_embedding,
)


class _FakeEmbedder:
    """Deterministic: embed each text as a hashed, dim-length float32 vector.

    Vectors are produced by hashing then mapping each byte into [-1, 1] so
    `_cosine` stays in [-1, 1] with no NaN/inf drama.
    """

    def __init__(self, model_name: str = "fake-v1", dim: int = 8) -> None:
        self.model_name = model_name
        self.dim = dim

    def embed(self, texts: list[str]) -> list[bytes]:
        out: list[bytes] = []
        for t in texts:
            digest = hashlib.sha256((self.model_name + "::" + t).encode()).digest()
            floats: list[float] = []
            for i in range(self.dim):
                b = digest[i % len(digest)]
                floats.append((b / 127.5) - 1.0)
            out.append(encode_vector(floats))
        return out

    def embed_source(self, name: str, description: str) -> bytes:
        return self.embed([build_source_text(name, description)])[0]


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "emb.db")
    initialize(c)
    create_inventory(c, InventorySpec(name="Note", description="", fields=()))
    return c


def _insert_node(conn: sqlite3.Connection, inventory: str, name: str) -> str:
    nid = new_id()
    conn.execute(
        f"INSERT INTO {table_name_for(inventory)}"
        " (id, name, description, owned_by, updated_by) VALUES (?, ?, ?, ?, ?)",
        (nid, name, "a description", ADMIN_USER_ID, ADMIN_USER_ID),
    )
    return nid


def test_encode_decode_roundtrip() -> None:
    v = [1.0, -2.5, 3.25, 0.0]
    blob = encode_vector(v)
    assert decode_vector(blob) == v


def test_build_source_text_concatenates() -> None:
    assert build_source_text("getUserName", "fetches the user") == "getUserName\nfetches the user"
    assert build_source_text("x", "") == "x\n"


def test_upsert_node_embedding_inserts(conn: sqlite3.Connection) -> None:
    emb = _FakeEmbedder()
    nid = _insert_node(conn, "Note", "n1")
    upsert_node_embedding(conn, emb, "Note", nid, "n1", "a description")
    row = conn.execute(
        "SELECT model, dim, vector, source_text FROM embeddings WHERE inventory=? AND node_id=?",
        ("Note", nid),
    ).fetchone()
    assert row["model"] == emb.model_name
    assert row["dim"] == emb.dim
    assert row["source_text"] == "n1\na description"
    assert len(decode_vector(row["vector"])) == emb.dim


def test_upsert_node_embedding_is_deterministic(conn: sqlite3.Connection) -> None:
    emb = _FakeEmbedder()
    nid = _insert_node(conn, "Note", "n1")
    upsert_node_embedding(conn, emb, "Note", nid, "n1", "d")
    first = conn.execute("SELECT vector FROM embeddings WHERE node_id=?", (nid,)).fetchone()
    upsert_node_embedding(conn, emb, "Note", nid, "n1", "d")
    second = conn.execute("SELECT vector FROM embeddings WHERE node_id=?", (nid,)).fetchone()
    assert first["vector"] == second["vector"]


def test_upsert_replaces_on_update(conn: sqlite3.Connection) -> None:
    emb = _FakeEmbedder()
    nid = _insert_node(conn, "Note", "n1")
    upsert_node_embedding(conn, emb, "Note", nid, "n1", "first")
    upsert_node_embedding(conn, emb, "Note", nid, "n1", "second")
    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM embeddings WHERE node_id=?", (nid,)
    ).fetchone()
    assert rows["c"] == 1
    row = conn.execute(
        "SELECT source_text FROM embeddings WHERE node_id=?", (nid,)
    ).fetchone()
    assert row["source_text"] == "n1\nsecond"


def test_iter_stale_embeddings_picks_only_outdated(conn: sqlite3.Connection) -> None:
    old = _FakeEmbedder(model_name="old-model")
    new = _FakeEmbedder(model_name="new-model")
    nid_a = _insert_node(conn, "Note", "a")
    nid_b = _insert_node(conn, "Note", "b")
    upsert_node_embedding(conn, old, "Note", nid_a, "a", "")
    upsert_node_embedding(conn, new, "Note", nid_b, "b", "")

    stale = iter_stale_embeddings(conn, "new-model")
    assert len(stale) == 1
    assert stale[0][1] == nid_a


def test_reembed_stale_updates_rows(conn: sqlite3.Connection) -> None:
    old = _FakeEmbedder(model_name="old-model")
    new = _FakeEmbedder(model_name="new-model")
    nid = _insert_node(conn, "Note", "a")
    upsert_node_embedding(conn, old, "Note", nid, "a", "")

    updated = reembed_stale(conn, new)
    assert updated == 1

    row = conn.execute(
        "SELECT model FROM embeddings WHERE node_id=?", (nid,)
    ).fetchone()
    assert row["model"] == "new-model"
    # Second call is a no-op.
    assert reembed_stale(conn, new) == 0


def test_vector_dim_matches_dim_column(conn: sqlite3.Connection) -> None:
    emb = _FakeEmbedder(dim=16)
    nid = _insert_node(conn, "Note", "x")
    upsert_node_embedding(conn, emb, "Note", nid, "x", "y")
    row = conn.execute("SELECT dim, vector FROM embeddings WHERE node_id=?", (nid,)).fetchone()
    assert row["dim"] == 16
    assert len(decode_vector(row["vector"])) == 16


# --- Real-model smoke test (optional) --------------------------------------


@pytest.mark.slow
def test_fastembed_real_model_matches_dim() -> None:
    from rag_coding_mcp.embeddings import FastEmbedEmbedder

    emb = FastEmbedEmbedder()
    vectors = emb.embed(["hello world"])
    assert len(vectors) == 1
    assert len(decode_vector(vectors[0])) == DEFAULT_DIM
