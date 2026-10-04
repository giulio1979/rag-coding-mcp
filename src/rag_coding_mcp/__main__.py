"""Entrypoint: `python -m rag_coding_mcp` runs the MCP server over streamable HTTP."""

from __future__ import annotations

import os
from pathlib import Path

from rag_coding_mcp.server import build_mcp, build_state


def main() -> None:
    db_path = Path(os.environ.get("RAG_DB_PATH", "/data/rag.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("RAG_HOST", "0.0.0.0")
    port = int(os.environ.get("RAG_PORT", "8080"))

    state = build_state(db_path)
    mcp = build_mcp(state)
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
