# rag-coding-mcp

Central, knowledge-only MCP server for coding agents.

One instance runs on the NAS (k3s). Editors on laptops connect to it as an MCP server; phones can read through a public read-only endpoint. The server stores repository overviews, file summaries, symbols, and SOPs — **it never sees source code**. Agents run builds and tests locally and report structured results back through MCP tools.

## Status

Pre-implementation. See [specs/001-core-mcp-server/spec.md](specs/001-core-mcp-server/spec.md) for the current design.

## How this repo works

This project uses **spec-driven development**. Read [AGENTS.md](AGENTS.md) before contributing (human or AI). In short:

1. Every feature starts as a numbered spec in `specs/NNN-slug/`.
2. `spec.md` → `plan.md` → `tasks.md` get reviewed before any code is written.
3. Tests are derived from acceptance criteria in `spec.md`.

## Stack

- Python 3.11+
- FastMCP
- SQLite (FTS5 + vector BLOBs)
- Server-side embeddings
