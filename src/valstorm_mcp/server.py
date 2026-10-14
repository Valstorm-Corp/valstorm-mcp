"""FastMCP and FastAPI server initialization for Valstorm Hosted MCP."""

import argparse
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from valstorm_mcp import __version__
from valstorm_mcp.auth import MCPAuthMiddleware
from valstorm_mcp.config import config
from valstorm_mcp.oauth_metadata import router as oauth_metadata_router
from valstorm_mcp.tools.records import (
    record_create as _record_create,
)
from valstorm_mcp.tools.records import (
    record_delete as _record_delete,
)
from valstorm_mcp.tools.records import (
    record_get as _record_get,
)
from valstorm_mcp.tools.records import (
    record_hydrate_batch as _record_hydrate_batch,
)
from valstorm_mcp.tools.records import (
    record_query_mongo as _record_query_mongo,
)
from valstorm_mcp.tools.records import (
    record_query_sql as _record_query_sql,
)
from valstorm_mcp.tools.records import (
    record_update as _record_update,
)
from valstorm_mcp.tools.records import (
    schema_inspect as _schema_inspect,
)
from valstorm_mcp.tools.vfs import (
    vfs_ask as _vfs_ask,
)
from valstorm_mcp.tools.vfs import (
    vfs_browse as _vfs_browse,
)
from valstorm_mcp.tools.vfs import (
    vfs_delete_file as _vfs_delete_file,
)
from valstorm_mcp.tools.vfs import (
    vfs_get_download_url as _vfs_get_download_url,
)
from valstorm_mcp.tools.vfs import (
    vfs_move as _vfs_move,
)
from valstorm_mcp.tools.vfs import (
    vfs_prepare_upload as _vfs_prepare_upload,
)
from valstorm_mcp.tools.vfs import (
    vfs_read_text as _vfs_read_text,
)
from valstorm_mcp.tools.vfs import (
    vfs_search as _vfs_search,
)

START_TIME = time.time()

# 1. Initialize FastMCP instance
mcp = FastMCP(
    name="valstorm_mcp_server",
    # Keep the transport path and the path baked into the discovery documents
    # derived from one value. Previously config.mcp_path fed discovery only,
    # so overriding it produced a server advertising a resource URI it did not
    # actually serve.
    streamable_http_path=config.mcp_path,
    # The hosted deployment runs multiple replicas behind a load balancer with no
    # session affinity. Stateful Streamable HTTP keeps its session table in process
    # memory, so a follow-up request routed to a different pod is answered with
    # HTTP 404 "Session not found" (see StreamableHTTPSessionManager). This service
    # is a stateless proxy to the Valstorm REST API and holds no per-session state
    # of its own, so running the transport statelessly costs nothing and removes
    # that failure mode entirely.
    stateless_http=True,
    # Reply to /mcp with a single JSON response rather than an SSE-framed one.
    # Simpler for clients and immune to ingress response buffering.
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# 2. Register VFS Tools
@mcp.tool()
async def vfs_search(query: str, limit: int = 10, vault_id: str | None = None) -> str:
    """Search files, metadata, and document vector chunks across the Valstorm workspace."""
    return await _vfs_search(query=query, limit=limit, vault_id=vault_id)


@mcp.tool()
async def vfs_ask(query: str, vault_id: str | None = None, limit: int = 5) -> str:
    """Ask natural language questions answered by workspace documents with server-side AI RAG synthesis."""
    return await _vfs_ask(query=query, vault_id=vault_id, limit=limit)


@mcp.tool()
async def vfs_read_text(file_id: str, max_bytes: int = 100_000) -> str:
    """Reads and inspects the text/markdown/CSV content of a VFS file directly."""
    return await _vfs_read_text(file_id=file_id, max_bytes=max_bytes)


@mcp.tool()
async def vfs_browse(target: str = "root", browse_type: str = "vault") -> str:
    """Explores Virtual File Service folders, file lists, and directory trees without downloading binary blobs."""
    return await _vfs_browse(target=target, browse_type=browse_type)


@mcp.tool()
async def vfs_move(item_id: str, to_vault_id: str) -> str:
    """Moves a file or vault folder to another vault destination."""
    return await _vfs_move(item_id=item_id, to_vault_id=to_vault_id)


@mcp.tool()
async def vfs_prepare_upload(
    filename: str,
    vault_id: str | None = None,
    content_type: str = "application/octet-stream",
) -> str:
    """Prepares an upload recipe and execution instructions for pushing files to Valstorm VFS."""
    return await _vfs_prepare_upload(filename=filename, vault_id=vault_id, content_type=content_type)


@mcp.tool()
async def vfs_get_download_url(file_id: str) -> str:
    """Retrieves metadata and a download recipe / presigned URL for a VFS file."""
    return await _vfs_get_download_url(file_id=file_id)


@mcp.tool()
async def vfs_delete_file(file_id: str, confirm: bool = False) -> str:
    """Deletes a file or item from the Virtual File System (requires confirm=True)."""
    return await _vfs_delete_file(file_id=file_id, confirm=confirm)


# 3. Register Records & SQL Tools
@mcp.tool()
async def record_query_sql(query: str, bypass_cache: bool = False, limit: int = 50) -> str:
    """Executes a custom Valstorm SQL query against workspace records and schemas."""
    return await _record_query_sql(query=query, bypass_cache=bypass_cache, limit=limit)


@mcp.tool()
async def record_query_mongo(collection: str, pipeline: list[dict[str, Any]]) -> str:
    """Executes a structured document aggregation pipeline query."""
    return await _record_query_mongo(collection=collection, pipeline=pipeline)


@mcp.tool()
async def record_hydrate_batch(ids: list[str]) -> str:
    """Resolves a list of record IDs across schemas into display names and schema types in a single batch call."""
    return await _record_hydrate_batch(ids=ids)


@mcp.tool()
async def record_get(api_name: str, record_id: str) -> str:
    """Retrieves a single record by its ID and object collection/schema name."""
    return await _record_get(api_name=api_name, record_id=record_id)


@mcp.tool()
async def record_create(api_name: str, record: dict[str, Any] | list[dict[str, Any]]) -> str:
    """Creates one or multiple records in a standard or custom object collection."""
    return await _record_create(api_name=api_name, record=record)


@mcp.tool()
async def record_update(api_name: str, record_id: str, data: dict[str, Any]) -> str:
    """Updates/patches fields on an existing record."""
    return await _record_update(api_name=api_name, record_id=record_id, data=data)


@mcp.tool()
async def record_delete(api_name: str, record_id: str, confirm: bool = False) -> str:
    """Deletes a record from an object collection (requires confirm=True)."""
    return await _record_delete(api_name=api_name, record_id=record_id, confirm=confirm)


@mcp.tool()
async def schema_inspect(api_name: str | None = None) -> str:
    """Discovers object definitions, available fields, relationship lookups, and validation rules."""
    return await _schema_inspect(api_name=api_name)


# 4. Build FastAPI application
# Initialize subapps and lifespan
_streamable_app = mcp.streamable_http_app()
_sse_app = mcp.sse_app()

@asynccontextmanager
async def app_lifespan(app_instance: FastAPI):
    async with mcp.session_manager.run():
        yield

def create_app() -> FastAPI:
    fastapi_app = FastAPI(
        title="Valstorm Hosted MCP Server",
        description="Official Model Context Protocol microservice proxy for the Valstorm Platform",
        version=__version__,
        lifespan=app_lifespan,
    )

    # Middleware order matters here. Starlette's add_middleware() prepends, and
    # the stack is applied in reverse, so the LAST registration ends up
    # outermost. CORS must be outermost: a 401 emitted by the auth middleware
    # would otherwise never pass back through CORSMiddleware, and a browser
    # client would see an opaque CORS failure instead of the challenge it needs
    # to parse to discover the authorization server.
    fastapi_app.add_middleware(MCPAuthMiddleware)

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        # Without this, JavaScript cannot read the challenge even when the
        # response itself is CORS-decorated.
        expose_headers=["WWW-Authenticate", "Mcp-Session-Id"],
    )

    # OAuth 2.1 discovery documents and the RFC 7591 registration shim.
    # Registered before the MCP transport routes so discovery stays reachable
    # without a token (MCPAuthMiddleware treats /.well-known/* as public).
    fastapi_app.include_router(oauth_metadata_router)

    @fastapi_app.get("/health", tags=["System"])
    async def health_check():
        return {
            "status": "healthy",
            "service": "valstorm-mcp-server",
            "version": __version__,
            "uptime_seconds": round(time.time() - START_TIME, 2),
            "environment": config.env,
            "profile": config.profile,
        }

    @fastapi_app.get("/status", tags=["System"])
    async def server_status():
        tools_list = [
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
        return {
            "service": "valstorm-mcp-server",
            "version": __version__,
            "environment": config.env,
            "base_url": config.base_url,
            "api_base_url": config.api_base_url,
            "tools_count": len(tools_list),
            "tools": tools_list,
        }

    # Register FastMCP Streamable HTTP & SSE routes
    for route in _streamable_app.routes:
        fastapi_app.router.routes.append(route)
    for route in _sse_app.routes:
        fastapi_app.router.routes.append(route)

    return fastapi_app


app = create_app()


def main():
    """CLI entrypoint supporting stdio, streamable-http, and sse modes."""
    parser = argparse.ArgumentParser(description="Valstorm Hosted MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "http"],
        default="stdio" if not sys.stdin.isatty() else "streamable-http",
        help="Transport mode: stdio (local processes), streamable-http (modern MCP standard), or sse",
    )
    parser.add_argument("--host", default=config.host, help="Host to bind the HTTP server (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=config.port, help="Port to bind the HTTP server (default: 8660)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        print(f"[valstorm-mcp-server] Starting Hosted MCP Server (Streamable HTTP & SSE) on http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
