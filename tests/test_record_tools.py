"""Unit tests for record, query, and schema tools."""

import json

import httpx
import pytest

from valstorm_mcp import proxy
from valstorm_mcp.tools.records import (
    record_create,
    record_delete,
    record_get,
    record_hydrate_batch,
    record_query_mongo,
    record_query_sql,
    record_update,
    schema_inspect,
)


@pytest.mark.asyncio
async def test_record_query_sql(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/query"
        body = json.loads(request.content)
        assert "SELECT" in body["query"]
        return httpx.Response(200, json=[{"id": "cont_1", "name": "John Doe"}])

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res = await record_query_sql(query="SELECT * FROM contact")
    parsed = json.loads(res)
    assert isinstance(parsed, list)
    assert parsed[0]["name"] == "John Doe"


@pytest.mark.asyncio
async def test_record_query_mongo(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/query/mongo"
        body = json.loads(request.content)
        assert body["collection"] == "contact"
        return httpx.Response(200, json=[{"_id": "usr_1", "count": 10}])

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    pipeline = [{"$group": {"_id": "$owner", "count": {"$sum": 1}}}]
    res = await record_query_mongo(collection="contact", pipeline=pipeline)
    parsed = json.loads(res)
    assert parsed[0]["count"] == 10


@pytest.mark.asyncio
async def test_record_hydrate_batch(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/object/hydrate-batch"
        body = json.loads(request.content)
        assert body["ids"] == ["cont_1", "deal_2"]
        return httpx.Response(200, json={"cont_1": {"name": "Alice", "schema": "contact"}})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res = await record_hydrate_batch(ids=["cont_1", "deal_2"])
    parsed = json.loads(res)
    assert parsed["cont_1"]["name"] == "Alice"


@pytest.mark.asyncio
async def test_schema_inspect(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/schema/contact":
            return httpx.Response(200, json={"name": "contact", "fields": ["name", "email"]})
        return httpx.Response(200, json={"schemas": ["contact", "deal"]})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    all_schemas = await schema_inspect()
    assert "contact" in json.loads(all_schemas)["schemas"]

    single_schema = await schema_inspect(api_name="contact")
    assert json.loads(single_schema)["name"] == "contact"


@pytest.mark.asyncio
async def test_record_create(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/object/contact"
        return httpx.Response(200, json={"id": "cont_new", "name": "Alice"})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res = await record_create(api_name="contact", record={"name": "Alice"})
    parsed = json.loads(res)
    assert parsed["id"] == "cont_new"


@pytest.mark.asyncio
async def test_record_update(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/object/deal"
        body = json.loads(request.content)
        assert body["id"] == "deal_123"
        assert body["stage"] == "Closed Won"
        return httpx.Response(200, json={"id": "deal_123", "stage": "Closed Won"})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res = await record_update(api_name="deal", record_id="deal_123", data={"stage": "Closed Won"})
    parsed = json.loads(res)
    assert parsed["stage"] == "Closed Won"


@pytest.mark.asyncio
async def test_record_delete_confirmation_and_execution(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/object/contact/cont_123"
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    # Without confirm=True
    unconfirmed = await record_delete(api_name="contact", record_id="cont_123", confirm=False)
    assert json.loads(unconfirmed)["status"] == "confirmation_required"

    # With confirm=True
    confirmed = await record_delete(api_name="contact", record_id="cont_123", confirm=True)
    assert json.loads(confirmed)["status"] == "deleted"


@pytest.mark.asyncio
async def test_record_get_success(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/query"
        body = json.loads(request.content)
        assert "cont_123" in body["query"]
        return httpx.Response(200, json=[{"id": "cont_123", "name": "Alice"}])

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res = await record_get(api_name="contact", record_id="cont_123")
    parsed = json.loads(res)
    assert parsed["id"] == "cont_123"
    assert parsed["name"] == "Alice"


@pytest.mark.asyncio
async def test_record_get_not_found(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res = await record_get(api_name="contact", record_id="cont_nonexistent")
    parsed = json.loads(res)
    assert parsed["status_code"] == 404


@pytest.mark.asyncio
async def test_record_tool_proxy_error_handling(mock_auth_context, monkeypatch):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "Database unavailable"})

    monkeypatch.setattr(proxy, "proxy_client", proxy.ProxyClient(transport=httpx.MockTransport(mock_handler)))

    res_query = await record_query_sql(query="SELECT * FROM contact")
    assert "Database unavailable" in res_query

    res_create = await record_create(api_name="contact", record={"name": "Alice"})
    assert "Database unavailable" in res_create

    res_update = await record_update(api_name="contact", record_id="c1", data={"name": "Alice"})
    assert "Database unavailable" in res_update

    res_delete = await record_delete(api_name="contact", record_id="c1", confirm=True)
    assert "Database unavailable" in res_delete


@pytest.mark.asyncio
async def test_record_write_scope_rejection(readonly_auth_context):
    with pytest.raises(PermissionError) as exc_info:
        await record_create(api_name="contact", record={"name": "Bob"})
    assert "Missing required scope 'record:write'" in str(exc_info.value)
