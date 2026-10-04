"""Server composition: FastMCP tools + HTTP `/graphql` endpoint sharing the schema cache.

Only two MCP tools are exposed:
  - `get_metamodel()` : current metamodel + SDL + embedding info.
  - `graphql(query, variables?)` : execute an arbitrary GraphQL document.

A plain HTTP `POST /graphql` endpoint uses the same schema and resolvers so
the admin UI can talk to the server with any GraphQL client. The compiled
UI is served under `/ui/`.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from graphql import graphql_sync, print_schema
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from rag_coding_mcp.actor import ACTOR_HEADER, resolve_actor
from rag_coding_mcp.db import connect, initialize
from rag_coding_mcp.db.metamodel_read import read_metamodel
from rag_coding_mcp.embeddings import DEFAULT_MODEL_NAME, EmbedderProtocol, FastEmbedEmbedder
from rag_coding_mcp.graphql import SchemaCache, SchemaContext

_DEFAULT_UI_DIR = Path(__file__).resolve().parent / "ui_dist"


@dataclass
class AppState:
    conn: sqlite3.Connection
    embedder: EmbedderProtocol | None
    cache: SchemaCache
    ui_dir: Path | None = None

    def context_for_actor(self, actor_id: str) -> SchemaContext:
        return SchemaContext(
            conn=self.conn,
            embedder=self.embedder,
            actor_id=actor_id,
            on_metamodel_change=self.cache.invalidate,
        )


def _metamodel_payload(state: AppState) -> dict[str, Any]:
    inventories, relations = read_metamodel(state.conn)
    return {
        "inventories": [
            {
                "name": inv.name,
                "description": inv.description,
                "fields": [
                    {
                        "name": f.name,
                        "type": f.type,
                        "required": f.required,
                        "enumValues": list(f.enum_values) if f.enum_values else None,
                        "description": f.description,
                    }
                    for f in inv.fields
                ],
            }
            for inv in inventories
        ],
        "relations": [
            {
                "name": r.name,
                "fromInventory": r.from_inventory,
                "toInventory": r.to_inventory,
                "cardinality": r.cardinality,
                "description": r.description,
            }
            for r in relations
        ],
        "embeddingModel": state.embedder.model_name if state.embedder else None,
        "embeddingDim": state.embedder.dim if state.embedder else None,
        "sdl": print_schema(state.cache.get()),
    }


def _execute_graphql(
    state: AppState, actor_id: str, query: str, variables: dict[str, Any] | None
) -> dict[str, Any]:
    schema = state.cache.get()
    result = graphql_sync(
        schema,
        query,
        variable_values=variables,
        context_value=state.context_for_actor(actor_id),
    )
    payload: dict[str, Any] = {}
    if result.errors:
        payload["errors"] = [
            {"message": str(e), "path": list(e.path) if e.path else None}
            for e in result.errors
        ]
    if result.data is not None:
        payload["data"] = result.data
    return payload


def build_mcp(state: AppState) -> FastMCP:
    mcp = FastMCP(name="rag-coding-mcp")

    @mcp.tool
    def get_metamodel() -> dict[str, Any]:
        """Return the current metamodel, GraphQL SDL and embedding info.

        Call this at the START of every session before using `graphql` — the
        schema is user-editable at runtime (new inventories, fields and
        relations can appear between sessions). Treat the returned SDL as the
        single source of truth for which queries and mutations are valid; do
        NOT assume field names from previous sessions.

        Repository-freshness convention: once per working day (and after any
        large change to a repo you are tracking), re-fetch the metamodel and
        then query the relevant `Repo` node to confirm its `updatedAt` and
        the `contains` / `defines` / `has_sop` edges still match reality;
        add/update/delete nodes via `graphql` mutations as needed.
        """
        return _metamodel_payload(state)

    @mcp.tool
    def graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GraphQL document against the live schema.

        The schema is generated from the current metamodel — ALWAYS obtain it
        via `get_metamodel` first in a new session and whenever an operation
        fails with "Cannot query field X" (that means the metamodel has
        changed since you last looked).

        Base columns on every inventory node: `id`, `name`, `description`,
        `ownedBy`, `updatedBy`, `createdAt`, `updatedAt`. There is NO
        `createdBy` field — attribution-at-creation uses `ownedBy`.

        Plural queries (`repos`, `files`, …) return `[XResult!]!` where each
        element is `{ node, score, matchedBy }`. Select node fields through
        `node { … }`, not at the top level.
        """
        return _execute_graphql(state, actor_id=_default_actor(state), query=query, variables=variables)

    @mcp.custom_route("/graphql", methods=["POST"])
    async def http_graphql(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"errors": [{"message": "invalid JSON"}]}, status_code=400)
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            return JSONResponse(
                {"errors": [{"message": "missing 'query'"}]}, status_code=400
            )
        variables = body.get("variables")
        if variables is not None and not isinstance(variables, dict):
            return JSONResponse(
                {"errors": [{"message": "'variables' must be an object"}]},
                status_code=400,
            )
        actor = resolve_actor(state.conn, request.headers.get(ACTOR_HEADER))
        payload = _execute_graphql(state, actor.id, query, variables)
        return JSONResponse(payload)

    @mcp.custom_route("/ui", methods=["GET"])
    async def ui_index_bare(_request: Request) -> Response:
        return _serve_ui_index(state)

    @mcp.custom_route("/ui/{path:path}", methods=["GET"])
    async def ui_asset(request: Request) -> Response:
        return _serve_ui_asset(state, request.path_params["path"])

    @mcp.custom_route("/graphql-ui", methods=["GET"])
    async def graphql_playground(_request: Request) -> Response:
        return Response(content=_GRAPHIQL_HTML, media_type="text/html")

    return mcp


def _serve_ui_index(state: AppState) -> Response:
    if state.ui_dir is None:
        return PlainTextResponse("UI not built", status_code=404)
    index = state.ui_dir / "index.html"
    if not index.is_file():
        return PlainTextResponse("UI not built", status_code=404)
    return FileResponse(index, media_type="text/html")


def _serve_ui_asset(state: AppState, rel: str) -> Response:
    if state.ui_dir is None:
        return PlainTextResponse("UI not built", status_code=404)
    if not rel:
        return _serve_ui_index(state)
    # Resolve + enforce containment to block path traversal.
    base = state.ui_dir.resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return PlainTextResponse("forbidden", status_code=403)
    if not target.is_file():
        # SPA fallback: unknown routes under /ui/* serve index.html.
        return _serve_ui_index(state)
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=mime or "application/octet-stream")


def _default_actor(state: AppState) -> str:
    actor = resolve_actor(state.conn, None)
    return actor.id


def build_state(
    db_path: Path,
    embedder: EmbedderProtocol | None = None,
    ui_dir: Path | None | str = "auto",
) -> AppState:
    conn = connect(db_path)
    initialize(conn)
    if embedder is None:
        embedder = FastEmbedEmbedder(DEFAULT_MODEL_NAME)
    resolved_ui = _resolve_ui_dir(ui_dir)
    return AppState(conn=conn, embedder=embedder, cache=SchemaCache(conn), ui_dir=resolved_ui)


def _resolve_ui_dir(ui_dir: Path | None | str) -> Path | None:
    if ui_dir is None:
        return None
    if ui_dir == "auto":
        env_dir = os.environ.get("RAG_UI_DIR")
        if env_dir:
            p = Path(env_dir)
            return p if p.is_dir() else None
        if _DEFAULT_UI_DIR.is_dir():
            return _DEFAULT_UI_DIR
        return None
    p = Path(ui_dir)
    return p if p.is_dir() else None


_GRAPHIQL_HTML = """<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <title>rag-coding-mcp · GraphiQL</title>
    <link rel=\"stylesheet\" href=\"https://unpkg.com/graphiql@3/graphiql.min.css\" />
    <style>html,body,#graphiql{height:100%;margin:0}</style>
  </head>
  <body>
    <div id=\"graphiql\">Loading…</div>
    <script
      crossorigin
      src=\"https://unpkg.com/react@18/umd/react.production.min.js\"
    ></script>
    <script
      crossorigin
      src=\"https://unpkg.com/react-dom@18/umd/react-dom.production.min.js\"
    ></script>
    <script
      crossorigin
      src=\"https://unpkg.com/graphiql@3/graphiql.min.js\"
    ></script>
    <script>
      const fetcher = GraphiQL.createFetcher({ url: '/graphql', credentials: 'include' });
      const root = ReactDOM.createRoot(document.getElementById('graphiql'));
      root.render(React.createElement(GraphiQL, { fetcher, defaultEditorToolsVisibility: true }));
    </script>
  </body>
</html>
"""


__all__ = ["AppState", "build_mcp", "build_state"]
