"""Tests for OAuth discovery, the registration shim, and stateless transport.

These cover the three things that previously prevented Claude (and any other
spec-compliant MCP client) from connecting:

1. Streamable HTTP ran statefully, so a request routed to a second replica was
   answered with 404 "Session not found".
2. The discovery documents described the upstream Valstorm API instead of this
   server, so resource/issuer validation failed.
3. The 401 challenge used non-standard parameters, so clients could not locate
   the metadata document.
"""

import json
from urllib.parse import urlparse

import httpx
import pytest

from valstorm_mcp.config import config
from valstorm_mcp.server import app

BASE = "http://localhost:8660"
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


# --- RFC 9728: protected resource metadata ---


@pytest.mark.asyncio
async def test_protected_resource_identifies_this_server_not_the_api():
    async with _client() as client:
        res = await client.get("/.well-known/oauth-protected-resource")
        assert res.status_code == 200
        data = res.json()

    # Must be this MCP server's canonical URI (RFC 8707), never the upstream API.
    assert data["resource"] == config.resource_url
    assert data["resource"].endswith(config.mcp_path)
    assert "api.valstorm.com" not in data["resource"]
    assert data["authorization_servers"] == [config.public_url]
    assert "vfs:read" in data["scopes_supported"]

    # The MCP spec forbids access tokens in the query string.
    assert data["bearer_methods_supported"] == ["header"]


@pytest.mark.asyncio
async def test_protected_resource_path_aware_variant_matches():
    """A client addressing /mcp derives the path-aware well-known URL."""
    async with _client() as client:
        bare = await client.get("/.well-known/oauth-protected-resource")
        aware = await client.get("/.well-known/oauth-protected-resource/mcp")

    assert aware.status_code == 200
    assert aware.json() == bare.json()


@pytest.mark.asyncio
async def test_resource_metadata_url_resolves():
    """The URL advertised in WWW-Authenticate must actually be served."""
    path = urlparse(config.resource_metadata_url).path
    async with _client() as client:
        res = await client.get(path)
    assert res.status_code == 200
    assert res.json()["resource"] == config.resource_url


# --- RFC 8414: authorization server metadata ---


@pytest.mark.asyncio
async def test_issuer_matches_the_origin_serving_the_document():
    """RFC 8414 requires issuer to match where the metadata is served from."""
    async with _client() as client:
        res = await client.get("/.well-known/oauth-authorization-server")
        assert res.status_code == 200
        data = res.json()

    assert data["issuer"] == config.public_url
    # Previously this claimed api.valstorm.com while being served from
    # mcp.valstorm.com, which strict clients reject outright.
    assert urlparse(data["issuer"]).netloc == urlparse(config.public_url).netloc


@pytest.mark.asyncio
async def test_advertised_capabilities_match_the_api_implementation():
    async with _client() as client:
        data = (await client.get("/.well-known/oauth-authorization-server")).json()

    assert data["authorization_endpoint"] == config.authorization_endpoint
    assert data["token_endpoint"] == config.token_endpoint
    assert data["response_types_supported"] == ["code"]
    # The API's token endpoint rejects any grant other than authorization_code;
    # advertising refresh_token would send clients down a path that 400s.
    assert data["grant_types_supported"] == ["authorization_code"]
    # oauth_service.py hardcodes SHA-256; there is no plain support.
    assert data["code_challenge_methods_supported"] == ["S256"]
    # PKCE public clients present no secret.
    assert "none" in data["token_endpoint_auth_methods_supported"]


@pytest.mark.asyncio
async def test_authorization_server_path_aware_variant():
    async with _client() as client:
        res = await client.get("/.well-known/oauth-authorization-server/mcp")
    assert res.status_code == 200
    assert res.json()["issuer"] == config.public_url


# --- RFC 7591: registration shim ---


@pytest.mark.asyncio
async def test_registration_returns_the_preprovisioned_public_client(monkeypatch):
    monkeypatch.setattr(config, "enable_dcr", True)
    async with _client() as client:
        res = await client.post(
            "/oauth2/register",
            json={
                "client_name": "Claude",
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )

    assert res.status_code == 201
    data = res.json()
    assert data["client_id"] == config.oauth_client_id
    assert data["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in data
    assert data["grant_types"] == ["authorization_code"]


@pytest.mark.asyncio
async def test_registration_is_reachable_without_a_token(monkeypatch):
    """Registration necessarily precedes holding any token."""
    monkeypatch.setattr(config, "require_auth", True)
    monkeypatch.setattr(config, "enable_dcr", True)

    async with _client() as client:
        res = await client.post(
            "/oauth2/register",
            json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]},
        )
    assert res.status_code == 201


@pytest.mark.asyncio
async def test_registration_rejects_redirect_uris_the_api_would_refuse(monkeypatch):
    monkeypatch.setattr(config, "enable_dcr", True)
    async with _client() as client:
        res = await client.post(
            "/oauth2/register",
            json={"redirect_uris": ["https://attacker.example.com/callback"]},
        )
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_redirect_uri"


@pytest.mark.asyncio
async def test_registration_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "enable_dcr", False)
    async with _client() as client:
        res = await client.post(
            "/oauth2/register",
            json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]},
        )
    assert res.status_code == 404


# --- 401 challenge ---


@pytest.mark.asyncio
async def test_challenge_points_at_the_resource_metadata_document(monkeypatch):
    monkeypatch.setattr(config, "require_auth", True)

    async with _client() as client:
        res = await client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})

    assert res.status_code == 401
    challenge = res.headers["WWW-Authenticate"]
    assert f'resource_metadata="{config.resource_metadata_url}"' in challenge
    # The previous non-standard parameters are gone.
    assert "authorization_uri=" not in challenge
    assert "token_uri=" not in challenge


@pytest.mark.asyncio
async def test_cors_preflight_is_not_challenged(monkeypatch):
    """A 401 here would never pass back through CORSMiddleware."""
    monkeypatch.setattr(config, "require_auth", True)

    async with _client() as client:
        res = await client.options(
            "/mcp",
            headers={
                "Origin": "https://claude.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert res.status_code != 401
    assert res.headers.get("access-control-allow-origin") is not None


# --- Stateless Streamable HTTP ---


def _decode(res: httpx.Response) -> dict:
    """Read a JSON-RPC result from either a JSON or SSE-framed response."""
    if res.headers.get("content-type", "").startswith("text/event-stream"):
        for line in res.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise AssertionError(f"no data frame in SSE response: {res.text!r}")
    return res.json()


@pytest.mark.asyncio
async def test_streamable_http_is_stateless(monkeypatch):
    """The three properties that make multi-replica serving safe.

    Asserted in one test on purpose: StreamableHTTPSessionManager.run() may only
    be entered once per instance, and `mcp` is a module-level singleton, so the
    lifespan cannot be opened separately by several tests.
    """
    monkeypatch.setattr(config, "require_auth", False)

    async with app.router.lifespan_context(app):
        async with _client() as client:
            # 1. A handshake must not hand out a session id. No session id means
            #    no session table, so no cross-replica lookup to miss.
            init = await client.post(
                "/mcp",
                headers=MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "1.0"},
                    },
                },
            )
            assert init.status_code == 200, init.text
            assert "mcp-session-id" not in {k.lower() for k in init.headers}, (
                "a session id means the stateful transport is back"
            )
            assert _decode(init)["result"]["serverInfo"]["name"] == "valstorm_mcp_server"

            # 2. A request sharing no session with any handshake must still work.
            #    This is the second-replica case; statefully it returned 404.
            listing = await client.post(
                "/mcp",
                headers=MCP_HEADERS,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
            assert listing.status_code == 200, listing.text
            tools = _decode(listing)["result"]["tools"]
            assert len(tools) == 16
            assert {"vfs_search", "record_query_sql", "schema_inspect"} <= {t["name"] for t in tools}

            # 3. A client replaying a session id from a pod that no longer exists
            #    must not be rejected.
            stale = await client.post(
                "/mcp",
                headers={**MCP_HEADERS, "Mcp-Session-Id": "session-from-a-pod-that-is-gone"},
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            )
            assert stale.status_code != 404, "stale session ids must not 404 in stateless mode"
            assert stale.status_code == 200, stale.text
