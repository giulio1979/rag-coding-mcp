"""Node write helpers: embedding upsert and stale-embedding reconciliation.

Node CRUD will be driven by the GraphQL layer later; these helpers keep the
persistence shape in one place so resolvers stay thin.
"""

from __future__ import annotations

import sqlite3

from rag_coding_mcp.embeddings import EmbedderProtocol, build_source_text


def upsert_node_embedding(
    conn: sqlite3.Connection,
    embedder: EmbedderProtocol,
    inventory: str,
    node_id: str,
    name: str,
    description: str,
) -> None:
    """Compute and store the embedding for a node, replacing any prior row."""
    source = build_source_text(name, description)
    vector = embedder.embed([source])[0]
    conn.execute(
        """
        INSERT INTO embeddings
            (inventory, node_id, model, dim, vector, source_text)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(inventory, node_id) DO UPDATE SET
            model=excluded.model,
            dim=excluded.dim,
            vector=excluded.vector,
            source_text=excluded.source_text
        """,
        (inventory, node_id, embedder.model_name, embedder.dim, vector, source),
    )


def iter_stale_embeddings(
    conn: sqlite3.Connection, current_model: str
) -> list[tuple[str, str, str]]:
    """Return (inventory, node_id, source_text) for every embedding not on the current model."""
    rows = conn.execute(
        "SELECT inventory, node_id, source_text FROM embeddings WHERE model != ?",
        (current_model,),
    ).fetchall()
    return [(r["inventory"], r["node_id"], r["source_text"]) for r in rows]


def reembed_stale(conn: sqlite3.Connection, embedder: EmbedderProtocol) -> int:
    """Re-embed every row whose `model` is not the current one. Returns count updated."""
    stale = iter_stale_embeddings(conn, embedder.model_name)
    if not stale:
        return 0
    texts = [s[2] for s in stale]
    vectors = embedder.embed(texts)
    with conn:
        for (inventory, node_id, _), vector in zip(stale, vectors, strict=True):
            conn.execute(
                """
                UPDATE embeddings
                   SET model=?, dim=?, vector=?
                 WHERE inventory=? AND node_id=?
                """,
                (embedder.model_name, embedder.dim, vector, inventory, node_id),
            )
    return len(stale)
