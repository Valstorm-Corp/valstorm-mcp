"""Unit tests for the httpx ProxyClient wrapper."""

import json

import httpx
import pytest

from valstorm_mcp.auth import AuthContext, current_auth
from valstorm_mcp.proxy import ProxyClient, ProxyClientError


@pytest.mark.asyncio
async def test_proxy_get_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/test-endpoint"
        assert request.headers.get("Authorization") == "Bearer token_abc"
        return httpx.Response(200, json={"status": "ok", "items": [1, 2, 3]})

    transport = httpx.MockTransport(handler)
    client = ProxyClient(base_url="https://api.valstorm.com/v1", transport=transport)

    ctx = AuthContext(token="token_abc", scopes={"*:*"})
    token_reset = current_auth.set(ctx)
    try:
        data = await client.get("test-endpoint")
        assert data == {"status": "ok", "items": [1, 2, 3]}
    finally:
        current_auth.reset(token_reset)


@pytest.mark.asyncio
async def test_proxy_post_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/object/contact"
        body = json.loads(request.content)
        assert body == {"name": "Alice"}
        return httpx.Response(200, json={"id": "cont_1", "name": "Alice"})

    transport = httpx.MockTransport(handler)
    client = ProxyClient(base_url="https://api.valstorm.com/v1", transport=transport)

    ctx = AuthContext(token="token_abc", scopes={"*:*"})
    token_reset = current_auth.set(ctx)
    try:
        data = await client.post("object/contact", json_data={"name": "Alice"})
        assert data["id"] == "cont_1"
    finally:
        current_auth.reset(token_reset)


@pytest.mark.asyncio
async def test_proxy_patch_and_delete_success():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(200, json={"updated": True})
        elif request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(400)

    transport = httpx.MockTransport(handler)
    client = ProxyClient(base_url="https://api.valstorm.com/v1", transport=transport)

    patch_res = await client.patch("object/contact", json_data={"id": "1", "name": "Bob"})
    assert patch_res == {"updated": True}

    del_res = await client.delete("object/contact/1")
    assert del_res["status"] == "success"


@pytest.mark.asyncio
async def test_proxy_401_error_handling():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Token expired"})

    transport = httpx.MockTransport(handler)
    client = ProxyClient(base_url="https://api.valstorm.com/v1", transport=transport)

    with pytest.raises(ProxyClientError) as exc_info:
        await client.get("some-path")
    assert exc_info.value.status_code == 401
    assert "Token expired" in exc_info.value.message


@pytest.mark.asyncio
async def test_proxy_403_and_404_error_handling():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/forbidden":
            return httpx.Response(403, json={"detail": "Missing permissions"})
        return httpx.Response(404, json={"detail": "Item not found"})

    transport = httpx.MockTransport(handler)
    client = ProxyClient(base_url="https://api.valstorm.com/v1", transport=transport)

    with pytest.raises(ProxyClientError) as exc_info:
        await client.get("forbidden")
    assert exc_info.value.status_code == 403

    with pytest.raises(ProxyClientError) as exc_info:
        await client.get("notfound")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_proxy_429_rate_limit_handling():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "Too many requests"})

    transport = httpx.MockTransport(handler)
    client = ProxyClient(base_url="https://api.valstorm.com/v1", transport=transport)

    with pytest.raises(ProxyClientError) as exc_info:
        await client.get("some-path")
    assert exc_info.value.status_code == 429
    assert "Rate limit exceeded" in exc_info.value.message


@pytest.mark.asyncio
async def test_proxy_500_error_handling():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    client = ProxyClient(base_url="https://api.valstorm.com/v1", transport=transport)

    with pytest.raises(ProxyClientError) as exc_info:
        await client.get("error-path")
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_proxy_stream_rag():
    sse_body = (
        "event: ai_chunk\n"
        "data: {\"delta\": \"Synthesized text\"}\n\n"
        "event: citations\n"
        "data: {\"citations\": [{\"filename\": \"test.md\"}]}\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    client = ProxyClient(base_url="https://api.valstorm.com/v1", transport=transport)

    events = []
    async for msg in client.stream_rag(query="test question"):
        events.append(msg)

    assert len(events) == 2
    assert events[0]["event"] == "ai_chunk"
    assert events[0]["data"]["delta"] == "Synthesized text"
    assert events[1]["event"] == "citations"
