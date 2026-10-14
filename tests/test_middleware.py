"""Tests for MCPAuthMiddleware and endpoint authentication."""

import time

import httpx
import jwt
import pytest

from valstorm_mcp.config import config
from valstorm_mcp.server import app, mcp


def _make_valid_jwt(payload: dict) -> str:
    return jwt.encode(payload, config.jwt_secret, algorithm="HS256")


@pytest.mark.asyncio
async def test_sse_missing_token_returns_401(monkeypatch):
    monkeypatch.setattr(config, "require_auth", True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8660") as client:
        res = await client.get("/sse")
        assert res.status_code == 401
        data = res.json()
        assert data["error"] == "Unauthorized"
        assert "WWW-Authenticate" in res.headers
        assert "resource_metadata" in res.headers["WWW-Authenticate"]


@pytest.mark.asyncio
async def test_messages_missing_token_returns_401(monkeypatch):
    monkeypatch.setattr(config, "require_auth", True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8660") as client:
        res = await client.post("/messages", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert res.status_code == 401
        assert "WWW-Authenticate" in res.headers


@pytest.mark.asyncio
async def test_options_preflight_bypasses_auth(monkeypatch):
    monkeypatch.setattr(config, "require_auth", True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8660") as client:
        headers = {
            "Origin": "https://claude.ai",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        }
        res = await client.options("/mcp", headers=headers)
        # OPTIONS should succeed (200) and return CORS headers
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == "*"


@pytest.mark.asyncio
async def test_mcp_streamable_with_valid_bearer_token(monkeypatch):
    monkeypatch.setattr(config, "require_auth", True)
    valid_token = _make_valid_jwt({"user_id": "usr_test", "scopes": ["*:*"], "exp": int(time.time()) + 3600})

    async with mcp.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8660") as client:
            headers = {
                "Authorization": f"Bearer {valid_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            res = await client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                    "id": 1,
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert "result" in data
