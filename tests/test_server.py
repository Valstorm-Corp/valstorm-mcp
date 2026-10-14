"""Tests for FastAPI HTTP endpoints and FastMCP tool registration."""

import json

import httpx
import pytest

from valstorm_mcp import proxy
from valstorm_mcp.server import (
    app,
    main,
    mcp,
    record_create,
    record_delete,
    record_get,
    record_hydrate_batch,
    record_query_mongo,
    record_query_sql,
    record_update,
    schema_inspect,
    vfs_browse,
    vfs_get_download_url,
    vfs_move,
    vfs_prepare_upload,
    vfs_read_text,
    vfs_search,
)


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8660") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["service"] == "valstorm-mcp-server"


@pytest.mark.asyncio
async def test_status_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8660") as client:
        res = await client.get("/status")
        assert res.status_code == 200
        data = res.json()
        assert data["tools_count"] == 16
        assert "vfs_search" in data["tools"]
        assert "vfs_browse" in data["tools"]
        assert "record_query_sql" in data["tools"]
        assert "record_query_mongo" in data["tools"]
        assert "record_hydrate_batch" in data["tools"]
        assert "schema_inspect" in data["tools"]


@pytest.mark.asyncio
async def test_tool_registry():
    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]
    expected_tools = [
        "vfs_search",
        "vfs_ask",
        "vfs_read_text",
        "vfs_browse",
        "vfs_move",
        "vfs_prepare_upload",
        "vfs_get_download_url",
        "vfs_delete_file",
        "record_query_sql",
        "record_query_mongo",
        "record_hydrate_batch",
        "record_get",
        "record_create",
        "record_update",
        "record_delete",
        "schema_inspect",
    ]
    for t in expected_tools:
        assert t in tool_names


@pytest.mark.asyncio
async def test_server_tools_direct_execution(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "/query/mongo" in request.url.path:
            return httpx.Response(200, json=[{"count": 5}])
        elif "/query" in request.url.path:
            return httpx.Response(200, json=[{"id": "c1", "name": "Test"}])
        elif "/search" in request.url.path:
            return httpx.Response(200, json={"results": []})
        elif "/vfs/vault/" in request.url.path:
            return httpx.Response(200, json={"vault_id": "root", "folders": []})
        elif "/vfs/move" in request.url.path:
            return httpx.Response(200, json={"success": True})
        elif "/vfs/file/" in request.url.path:
            return httpx.Response(200, json={"id": "f1", "delivery_type": "inline_text", "text_content": "hello"})
        elif "/object/hydrate-batch" in request.url.path:
            return httpx.Response(200, json={"c1": {"name": "Test"}})
        elif "/schema" in request.url.path:
            return httpx.Response(200, json={"schemas": ["contact"]})
        elif "/object/contact" in request.url.path:
            if request.method == "POST":
                return httpx.Response(200, json={"id": "c_new"})
            elif request.method == "PATCH":
                return httpx.Response(200, json={"id": "c_new", "updated": True})
            elif request.method == "DELETE":
                return httpx.Response(200, json={"deleted": True})
        return httpx.Response(200, json={})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res_search = await vfs_search(query="test")
    assert "results" in json.loads(res_search)

    res_read = await vfs_read_text(file_id="f1")
    assert res_read == "hello"

    res_browse = await vfs_browse()
    assert json.loads(res_browse)["vault_id"] == "root"

    res_move = await vfs_move(item_id="f1", to_vault_id="vaul_2")
    assert json.loads(res_move)["status"] == "moved"

    res_upload = await vfs_prepare_upload(filename="doc.txt")
    assert json.loads(res_upload)["status"] == "ready"

    res_dl = await vfs_get_download_url(file_id="f1")
    assert "download_url" in json.loads(res_dl)

    res_sql = await record_query_sql(query="SELECT * FROM contact")
    assert json.loads(res_sql)[0]["name"] == "Test"

    res_mongo = await record_query_mongo(collection="contact", pipeline=[])
    assert json.loads(res_mongo)[0]["count"] == 5

    res_hydrate = await record_hydrate_batch(ids=["c1"])
    assert json.loads(res_hydrate)["c1"]["name"] == "Test"

    res_schema = await schema_inspect()
    assert "schemas" in json.loads(res_schema)

    res_get = await record_get(api_name="contact", record_id="c1")
    assert json.loads(res_get)["name"] == "Test"

    res_create = await record_create(api_name="contact", record={"name": "Alice"})
    assert json.loads(res_create)["id"] == "c_new"

    res_update = await record_update(api_name="contact", record_id="c_new", data={"name": "Alice2"})
    assert json.loads(res_update)["updated"] is True

    res_del = await record_delete(api_name="contact", record_id="c_new", confirm=True)
    assert json.loads(res_del)["status"] == "deleted"


@pytest.mark.asyncio
async def test_oauth_discovery_endpoints():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8660") as client:
        res_auth = await client.get("/.well-known/oauth-authorization-server")
        assert res_auth.status_code == 200
        auth_data = res_auth.json()
        assert "authorization_endpoint" in auth_data
        assert "token_endpoint" in auth_data
        assert "S256" in auth_data["code_challenge_methods_supported"]

        res_res = await client.get("/.well-known/oauth-protected-resource")
        assert res_res.status_code == 200
        res_data = res_res.json()
        assert "scopes_supported" in res_data
        assert "vfs:read" in res_data["scopes_supported"]


@pytest.mark.asyncio
async def test_streamable_http_endpoint_auth_challenge(monkeypatch):
    from valstorm_mcp.config import config
    monkeypatch.setattr(config, "require_auth", True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8660") as client:
        res = await client.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert res.status_code == 401
        assert "WWW-Authenticate" in res.headers


def test_main_cli_dispatch(monkeypatch):
    import sys

    import uvicorn

    ran_mode = {}

    def mock_run_stdio(transport):
        ran_mode["mode"] = transport

    def mock_uvicorn_run(app_obj, host, port):
        ran_mode["mode"] = "sse"
        ran_mode["host"] = host
        ran_mode["port"] = port

    monkeypatch.setattr(mcp, "run", mock_run_stdio)
    monkeypatch.setattr(uvicorn, "run", mock_uvicorn_run)

    # Test stdio flag
    monkeypatch.setattr(sys, "argv", ["valstorm-mcp", "--transport", "stdio"])
    main()
    assert ran_mode["mode"] == "stdio"

    # Test sse flag
    monkeypatch.setattr(sys, "argv", ["valstorm-mcp", "--transport", "sse", "--port", "8660"])
    main()
    assert ran_mode["mode"] == "sse"
    assert ran_mode["port"] == 8660
