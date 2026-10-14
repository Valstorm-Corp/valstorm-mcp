"""Asynchronous HTTP proxy client forwarding tool calls to the core Valstorm REST API via valstorm-client SDK."""

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from valstorm import AsyncValstormClient, AuthProvider
from valstorm.exceptions import (
    RequestTimeoutError,
    TransportError,
    ValstormError,
    ValstormHttpError,
)
from valstorm_mcp.auth import (
    get_active_auth_context,
    get_valid_mcp_auth_context,
    mcp_internal_auth_context,
)
from valstorm_mcp.config import config


class ProxyClientError(ValstormError):
    """Base exception for proxy client communication failures."""

    def __init__(self, message: str, status_code: int | None = None, data: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.data = data


class MCPAuthProvider(AuthProvider):
    """Dynamic auth provider for Valstorm MCP Server.

    Resolves bearer token from active request context (current_auth),
    falling back to MCP internal refreshable auth context.
    """

    def get_auth_headers(self) -> dict[str, str]:
        ctx = get_active_auth_context()
        if ctx and ctx.token:
            return {"Authorization": f"Bearer {ctx.token}"}
        if mcp_internal_auth_context and mcp_internal_auth_context.token and not mcp_internal_auth_context.is_expired():
            return {"Authorization": f"Bearer {mcp_internal_auth_context.token}"}
        return {}

    async def get_auth_headers_async(self) -> dict[str, str]:
        ctx = get_active_auth_context()
        if ctx and ctx.token:
            return {"Authorization": f"Bearer {ctx.token}"}
        internal_ctx = await get_valid_mcp_auth_context()
        if internal_ctx and internal_ctx.token:
            return {"Authorization": f"Bearer {internal_ctx.token}"}
        return {}


class ProxyClient:
    """Stateless proxy forwarding requests to the Valstorm API using AsyncValstormClient."""

    def __init__(
        self,
        base_url: str | None = None,
        default_timeout: float | None = None,
        rag_timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        auth: AuthProvider | None = None,
    ):
        self._base_url = (base_url or config.base_url).rstrip("/")
        self._default_timeout = default_timeout or config.default_timeout
        self._rag_timeout = rag_timeout or config.rag_timeout
        self._transport = transport

        http_client = (
            httpx.AsyncClient(transport=transport, timeout=self._default_timeout)
            if transport is not None
            else None
        )
        self.sdk_client = AsyncValstormClient(
            base_url=self._base_url,
            auth=auth or MCPAuthProvider(),
            timeout=self._default_timeout,
            http_client=http_client,
        )

        # Expose official 1:1 domain facades
        self.records = self.sdk_client.records
        self.query = self.sdk_client.query
        self.schemas = self.sdk_client.schemas
        self.vfs = self.sdk_client.vfs
        self.files = self.sdk_client.files
        self.apps = self.sdk_client.apps
        self.sandboxes = self.sdk_client.sandboxes

    def _map_http_error(self, e: ValstormHttpError) -> ProxyClientError:
        status = e.status_code
        detail = e.message
        if status == 401:
            msg = f"Authentication failed (401 Unauthorized): {detail}"
        elif status == 403:
            msg = f"Permission denied (403 Forbidden): {detail}"
        elif status == 404:
            msg = f"Resource not found (404 Not Found): {detail}"
        elif status == 429:
            msg = f"Rate limit exceeded (429 Too Many Requests): Please back off and retry. {detail}"
        elif status >= 500:
            msg = f"Upstream Valstorm API error ({status}): {detail}"
        else:
            msg = f"Request failed with status {status}: {detail}"
        return ProxyClientError(msg, status_code=status, data=e.details)

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        try:
            res = await self.sdk_client.transport.request(
                "GET", path, params=params
            )
            if res.status_code == 204 or res.data is None:
                return {"status": "success", "message": "No content"}
            return res.data
        except ValstormHttpError as e:
            raise self._map_http_error(e) from e
        except RequestTimeoutError as e:
            t = timeout or self._default_timeout
            raise ProxyClientError(f"Request to '{path}' timed out after {t}s", status_code=504) from e
        except (TransportError, Exception) as e:
            raise ProxyClientError(f"Network error communicating with Valstorm API: {e!s}") from e

    async def post(
        self,
        path: str,
        json_data: dict[str, Any] | list | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        try:
            res = await self.sdk_client.transport.request(
                "POST", path, params=params, json=json_data
            )
            if res.status_code == 204 or res.data is None:
                return {"status": "success", "message": "No content"}
            return res.data
        except ValstormHttpError as e:
            raise self._map_http_error(e) from e
        except RequestTimeoutError as e:
            t = timeout or self._default_timeout
            raise ProxyClientError(f"Request to '{path}' timed out after {t}s", status_code=504) from e
        except (TransportError, Exception) as e:
            raise ProxyClientError(f"Network error communicating with Valstorm API: {e!s}") from e

    async def patch(
        self,
        path: str,
        json_data: dict[str, Any] | list | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        try:
            res = await self.sdk_client.transport.request(
                "PATCH", path, params=params, json=json_data
            )
            if res.status_code == 204 or res.data is None:
                return {"status": "success", "message": "No content"}
            return res.data
        except ValstormHttpError as e:
            raise self._map_http_error(e) from e
        except RequestTimeoutError as e:
            t = timeout or self._default_timeout
            raise ProxyClientError(f"Request to '{path}' timed out after {t}s", status_code=504) from e
        except (TransportError, Exception) as e:
            raise ProxyClientError(f"Network error communicating with Valstorm API: {e!s}") from e

    async def delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        try:
            res = await self.sdk_client.transport.request(
                "DELETE", path, params=params
            )
            if res.status_code == 204 or res.data is None:
                return {"status": "success", "message": "No content"}
            return res.data
        except ValstormHttpError as e:
            raise self._map_http_error(e) from e
        except RequestTimeoutError as e:
            t = timeout or self._default_timeout
            raise ProxyClientError(f"Request to '{path}' timed out after {t}s", status_code=504) from e
        except (TransportError, Exception) as e:
            raise ProxyClientError(f"Network error communicating with Valstorm API: {e!s}") from e

    async def stream_rag(
        self,
        query: str,
        vault_ids: list | None = None,
        limit: int = 5,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streams Server-Sent Events from /search/stream for live RAG generation."""
        url = self.sdk_client.transport._build_url("search/stream")
        auth_headers = await self.sdk_client.auth.get_auth_headers_async()
        headers = self.sdk_client.transport._prepare_headers(
            auth_headers, {"Accept": "text/event-stream"}
        )
        payload = {
            "query": query,
            "vault_ids": vault_ids,
            "limit": limit,
            "enable_rag": True,
        }

        client = self.sdk_client.transport.client
        try:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if not response.is_success:
                    body = await response.aread()
                    yield {"event": "error", "data": {"status": response.status_code, "message": body.decode()}}
                    return

                current_event = "message"
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        raw_data = line[5:].strip()
                        try:
                            parsed = json.loads(raw_data)
                        except Exception:
                            parsed = raw_data
                        yield {"event": current_event, "data": parsed}
                        current_event = "message"
        except Exception as e:
            yield {"event": "error", "data": {"message": str(e)}}


# Global default proxy client instance
proxy_client = ProxyClient()
