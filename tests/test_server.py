"""Tests for the MCP server composition (tasks 30-33)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from rag_coding_mcp.actor import ACTOR_HEADER
from rag_coding_mcp.graphql import SchemaCache
from rag_coding_mcp.server import AppState, build_mcp, build_state
from tests.test_embeddings import _FakeEmbedder


@pytest.fixture()
def state(tmp_path: Path) -> AppState:
    s = build_state(tmp_path / "s.db", embedder=_FakeEmbedder(), ui_dir=None)
    assert isinstance(s.cache, SchemaCache)
    return s


@pytest.fixture()
def state_with_ui(tmp_path: Path) -> AppState:
    ui = tmp_path / "ui_dist"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><html><body>hello ui</body></html>")
    (ui / "assets").mkdir()
    (ui / "assets" / "main.js").write_text("console.log('ok')")
    return build_state(tmp_path / "s.db", embedder=_FakeEmbedder(), ui_dir=ui)


# --- MCP tool surface -----------------------------------------------------


def test_exposes_exactly_two_tools(state: AppState) -> None:
    mcp = build_mcp(state)
    names = _list_tool_names(mcp)
    assert set(names) == {"get_metamodel", "graphql"}


def test_get_metamodel_tool(state: AppState) -> None:
    mcp = build_mcp(state)
    payload = _call_tool(mcp, "get_metamodel")
    assert {"inventories", "relations", "sdl", "embeddingModel", "embeddingDim"} <= payload.keys()
    assert payload["embeddingModel"] == "fake-v1"
    names = {inv["name"] for inv in payload["inventories"]}
    assert {"Repo", "File", "Method", "SOP"} <= names


def test_graphql_tool_runs_query(state: AppState) -> None:
    mcp = build_mcp(state)
    result = _call_tool(
        mcp,
        "graphql",
        query="{ metamodel { inventories { name } } }",
    )
    assert "errors" not in result
    names = {inv["name"] for inv in result["data"]["metamodel"]["inventories"]}
    assert "Repo" in names


def test_graphql_tool_runs_mutation(state: AppState) -> None:
    mcp = build_mcp(state)
    r1 = _call_tool(mcp, "graphql", query='mutation { createRepo(input: { name: "r1" }) { id ownedBy } }')
    assert "errors" not in r1, r1
    assert r1["data"]["createRepo"]["ownedBy"] == "admin"


def test_graphql_tool_metamodel_mutation_rebuilds(state: AppState) -> None:
    mcp = build_mcp(state)
    r = _call_tool(mcp, "graphql", query='mutation { createInventory(name: "Widget") }')
    assert "errors" not in r, r
    sdl_after = _call_tool(mcp, "get_metamodel")["sdl"]
    assert "type Widget" in sdl_after


def test_graphql_tool_user_mutation(state: AppState) -> None:
    mcp = build_mcp(state)
    r = _call_tool(mcp, "graphql", query='mutation { createUser(name: "alice") { name } }')
    assert "errors" not in r, r
    assert r["data"]["createUser"]["name"] == "alice"


# --- HTTP endpoint --------------------------------------------------------


def _http_client(state: AppState) -> TestClient:
    mcp = build_mcp(state)
    return TestClient(mcp.http_app())


def test_http_graphql_query(state: AppState) -> None:
    with _http_client(state) as client:
        resp = client.post("/graphql", json={"query": "{ metamodel { embeddingModel } }"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "errors" not in body
        assert body["data"]["metamodel"]["embeddingModel"] == "fake-v1"


def test_http_graphql_uses_actor_header(state: AppState) -> None:
    with _http_client(state) as client:
        created = client.post(
            "/graphql",
            json={"query": 'mutation { createUser(name: "alice") { id } }'},
        )
        alice_id = created.json()["data"]["createUser"]["id"]
        resp = client.post(
            "/graphql",
            headers={ACTOR_HEADER: alice_id},
            json={"query": 'mutation { createRepo(input: { name: "r" }) { ownedBy } }'},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["createRepo"]["ownedBy"] == alice_id


def test_http_graphql_bad_request(state: AppState) -> None:
    with _http_client(state) as client:
        resp = client.post("/graphql", json={})
        assert resp.status_code == 400
        resp2 = client.post("/graphql", content=b"not json")
        assert resp2.status_code == 400


# --- UI mount -------------------------------------------------------------


def test_ui_disabled_returns_404(state: AppState) -> None:
    with _http_client(state) as client:
        assert client.get("/ui").status_code == 404
        assert client.get("/ui/anything").status_code == 404


def test_ui_serves_index(state_with_ui: AppState) -> None:
    with _http_client(state_with_ui) as client:
        resp = client.get("/ui")
        assert resp.status_code == 200
        assert "hello ui" in resp.text
        assert resp.headers["content-type"].startswith("text/html")


def test_ui_serves_asset(state_with_ui: AppState) -> None:
    with _http_client(state_with_ui) as client:
        resp = client.get("/ui/assets/main.js")
        assert resp.status_code == 200
        assert "console.log" in resp.text
        assert "javascript" in resp.headers["content-type"]


def test_ui_spa_fallback(state_with_ui: AppState) -> None:
    with _http_client(state_with_ui) as client:
        resp = client.get("/ui/unknown-route")
        assert resp.status_code == 200
        assert "hello ui" in resp.text


def test_ui_blocks_path_traversal(state_with_ui: AppState) -> None:
    with _http_client(state_with_ui) as client:
        resp = client.get("/ui/../etc/passwd")
        assert resp.status_code in (403, 404)


def test_graphql_playground(state: AppState) -> None:
    with _http_client(state) as client:
        resp = client.get("/graphql-ui")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "GraphiQL" in resp.text


# --- helpers --------------------------------------------------------------


def _list_tool_names(mcp) -> list[str]:
    result = _await(mcp.list_tools())
    return [t.name for t in result]


def _call_tool(mcp, name: str, **kwargs) -> dict:
    result = _await(mcp.call_tool(name, kwargs))
    # FastMCP returns a ToolResult-like object; prefer `.structured_content`,
    # fall back to `.content[0].text` (JSON) if that is not available.
    if hasattr(result, "structured_content") and result.structured_content is not None:
        sc = result.structured_content
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    import json as _json
    content = result.content[0]
    return _json.loads(content.text)


def _await(x):
    if inspect.isawaitable(x):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(x) if asyncio.get_event_loop().is_running() is False else asyncio.run(x)
    return x
