"""Authentication, token introspection, and scope enforcement for Valstorm MCP."""

import asyncio
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from valstorm_mcp.config import config

REFRESH_BEFORE_SECONDS = 60.0  # Refresh token if it expires in the next 60 seconds


@dataclass
class AuthContext:
    token: str
    user_id: str | None = None
    email: str | None = None
    organization_id: str | None = None
    scopes: set[str] = field(default_factory=set)
    is_admin: bool = False
    expires_at: float | None = None
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        """Checks if token has expired."""
        if self.expires_at is not None and time.time() > self.expires_at:
            return True
        return False

    def is_near_expiry(self) -> bool:
        """Checks if token is near expiration (within REFRESH_BEFORE_SECONDS)."""
        if self.expires_at is None:
            return False
        return self.expires_at - time.time() < REFRESH_BEFORE_SECONDS

    def has_scope(self, required_scope: str) -> bool:
        """Checks if context possesses the required scope or wildcard permissions."""
        if self.is_expired():
            return False
        if self.is_admin or "*:*" in self.scopes or "*" in self.scopes:
            return True
        if required_scope in self.scopes:
            return True

        # Resource wildcard check: e.g. 'vfs:*' matches 'vfs:read'
        if ":" in required_scope:
            resource, action = required_scope.split(":", 1)
            if f"{resource}:*" in self.scopes:
                return True

        return False


# Context variable for holding active auth per request/task
current_auth: ContextVar[AuthContext | None] = ContextVar("current_auth", default=None)


class AuthSessionRegistry:
    """Thread-safe in-memory session registry mapping SSE session IDs to AuthContext."""

    def __init__(self, ttl_seconds: float = 86400.0):
        self._sessions: dict[str, AuthContext] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl = ttl_seconds

    def register(self, session_id: str, auth_ctx: AuthContext) -> None:
        self._cleanup()
        self._sessions[session_id] = auth_ctx
        self._timestamps[session_id] = time.time()

    def get(self, session_id: str) -> AuthContext | None:
        self._cleanup()
        ctx = self._sessions.get(session_id)
        if ctx and ctx.is_expired():
            self.remove(session_id)
            return None
        return ctx

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._timestamps.pop(session_id, None)

    def _cleanup(self) -> None:
        now = time.time()
        expired = [sid for sid, ts in self._timestamps.items() if now - ts > self._ttl]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._timestamps.pop(sid, None)


session_registry = AuthSessionRegistry()


# Global, process-level state for the MCP's own internal token
mcp_internal_auth_context: AuthContext | None = None
mcp_auth_lock = asyncio.Lock()


async def get_valid_mcp_auth_context() -> AuthContext | None:
    """
    Retrieves the MCP's internal Valstorm API access token, ensuring it is refreshed if near expiry.

    This function must be called from an async context (e.g., in apps/valstorm-mcp-server/src/valstorm_mcp/proxy.py).
    """
    global mcp_internal_auth_context

    # 1. Check if we have an unexpired, not-near-expiry token
    if mcp_internal_auth_context and not mcp_internal_auth_context.is_expired() and not mcp_internal_auth_context.is_near_expiry():
        return mcp_internal_auth_context

    # 2. Check if we have a refresh token configured
    if config.refresh_token is None:
        if mcp_internal_auth_context and not mcp_internal_auth_context.is_expired():
             # If we have an existing token, but no refresh token, return it (it will fail soon).
            return mcp_internal_auth_context
        return None # No token and no refresh mechanism

    # 3. Token is missing or near expiry. Acquire lock and refresh.
    async with mcp_auth_lock:
        # Re-check state after acquiring the lock
        if mcp_internal_auth_context and not mcp_internal_auth_context.is_expired() and not mcp_internal_auth_context.is_near_expiry():
            return mcp_internal_auth_context

        # Check if this is the first load
        if mcp_internal_auth_context is None:
            # First attempt: treat the refresh token as a (potentially expired) access token
            # to populate the initial context (user_id, org_id, scopes) needed for refresh call.
            mcp_internal_auth_context = create_auth_context_from_token(config.refresh_token)
            if mcp_internal_auth_context and not mcp_internal_auth_context.is_expired():
                 # If the refresh token is still a valid access token, return it for now.
                print(f"[auth] Initial internal context created from refresh token. Expires: {time.ctime(mcp_internal_auth_context.expires_at)}")
                return mcp_internal_auth_context

            # If initial context creation failed or is expired, we proceed to refresh.

        print("[auth] Token is expired or near expiry. Triggering proactive token refresh...")

        refresh_url = f"{config.base_url}/v1/oauth2/refresh"
        # The API requires a Bearer token in the header, which is the long-lived refresh token
        headers = {"Authorization": f"Bearer {config.refresh_token}"}

        try:
            async with httpx.AsyncClient(timeout=config.default_timeout) as client:
                response = await client.post(refresh_url, headers=headers)
                response.raise_for_status()

            data = response.json()
            new_token = data.get("access_token")

            if not new_token:
                print(f"[auth] Refresh failed: Missing access_token in response: {data}")
                return None

            new_ctx = create_auth_context_from_token(new_token)
            if new_ctx:
                mcp_internal_auth_context = new_ctx
                print(f"[auth] Token refresh successful. New token expires at {time.ctime(new_ctx.expires_at)}")
                return new_ctx
            else:
                print("[auth] Refresh failed: Could not create AuthContext from new token.")
                return None

        except httpx.HTTPStatusError as e:
            print(f"[auth] HTTP Error during token refresh ({e.response.status_code}): {e.response.text}. Response: {e.response.text}")
            return None
        except Exception as e:
            print(f"[auth] Unexpected error during token refresh: {e}")
            return None


def parse_jwt_payload(token: str) -> dict[str, Any] | None:
    """Decodes and cryptographically verifies JWT payload claims and signature."""
    try:
        return jwt.decode(
            token,
            config.jwt_secret,
            algorithms=["HS256"],
            options={"verify_exp": True, "verify_signature": True},
        )
    except jwt.PyJWTError:
        return None
    except Exception:
        return None


def create_auth_context_from_token(token: str) -> AuthContext | None:
    """Constructs an AuthContext from a cryptographically verified JWT."""
    token = token.strip()
    payload = parse_jwt_payload(token)
    if not payload:
        return None

    exp_val = payload.get("exp")
    if exp_val is not None and float(exp_val) < time.time():
        return None

    raw_scopes = payload.get("scopes") or payload.get("scope") or []
    scopes_set: set[str] = set()
    if isinstance(raw_scopes, list):
        scopes_set = {str(s).strip() for s in raw_scopes if s}
    elif isinstance(raw_scopes, str):
        scopes_set = {s.strip() for s in raw_scopes.replace(",", " ").split() if s.strip()}

    is_admin = bool(
        payload.get("is_admin", False)
        or payload.get("role") == "admin"
        or "admin" in scopes_set
        or "*:*" in scopes_set
    )

    # If no explicit scopes found and token looks like a standard user JWT, default to full standard scopes
    if not scopes_set:
        scopes_set = {
            "vfs:read",
            "vfs:write",
            "vfs:delete",
            "record:read",
            "record:write",
            "record:delete",
            "query:execute",
        }

    return AuthContext(
        token=token,
        user_id=payload.get("user_id") or payload.get("sub") or payload.get("id") or payload.get("user"),
        email=payload.get("email"),
        organization_id=payload.get("organization_id") or payload.get("org_id") or payload.get("org"),
        scopes=scopes_set,
        is_admin=is_admin,
        expires_at=float(exp_val) if exp_val else None,
    )


def extract_token_from_request(request: Request) -> str | None:
    """Extracts token from Authorization header or ?token= query parameter."""
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    # Query param ?token= fallback for backward compatibility
    query_token = request.query_params.get("token")
    if query_token:
        return query_token.strip()

    return None


def get_active_auth_context() -> AuthContext | None:
    """Retrieves current auth context from ContextVar."""
    ctx = current_auth.get()
    if ctx and ctx.is_expired():
        return None
    return ctx


def require_scope(required_scope: str) -> None:
    """Enforces scope permission. Raises PermissionError if not authorized."""
    ctx = get_active_auth_context()
    if not ctx:
        if config.require_auth:
            raise PermissionError("Unauthorized: No active authentication token provided.")
        return

    if not ctx.has_scope(required_scope):
        raise PermissionError(
            f"Forbidden: Missing required scope '{required_scope}'. "
            f"Granted scopes: {list(ctx.scopes)}"
        )


PUBLIC_PATHS = {"/health", "/status", "/", "", "/oauth2/register"}
PUBLIC_PATH_PREFIXES = ("/.well-known/",)


class MCPAuthMiddleware:
    """ASGI Middleware to authenticate SSE handshakes, JSON-RPC calls, and Streamable HTTP."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        # 1. Immediate bypass for OPTIONS preflight requests (preserves CORS negotiation)
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # 2. Public endpoints (health, status, root, oauth well-known, registration)
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PATH_PREFIXES):
            await self.app(scope, receive, send)
            return

        # RFC 9728 section 5.1 challenge format
        auth_challenge_headers = {
            "WWW-Authenticate": (
                f'Bearer realm="{config.public_url}", '
                f'resource_metadata="{config.resource_metadata_url}"'
            )
        }

        # 3. SSE Connection Handshake: GET /sse
        if path == "/sse" and method == "GET":
            token = extract_token_from_request(request)
            auth_ctx = create_auth_context_from_token(token) if token else None

            if not auth_ctx and config.require_auth:
                response = JSONResponse(
                    {"error": "Unauthorized", "detail": "Missing or invalid Bearer token for SSE connection."},
                    status_code=401,
                    headers=auth_challenge_headers,
                )
                await response(scope, receive, send)
                return

            token_reset = current_auth.set(auth_ctx)
            try:
                await self.app(scope, receive, send)
            finally:
                current_auth.reset(token_reset)
            return

        # 4. JSON-RPC Message Dispatch: POST /messages
        if path.startswith("/messages") and method == "POST":
            session_id = request.query_params.get("session_id")
            token = extract_token_from_request(request)

            auth_ctx = None
            if token:
                auth_ctx = create_auth_context_from_token(token)
                if session_id and auth_ctx:
                    session_registry.register(session_id, auth_ctx)
            elif session_id:
                auth_ctx = session_registry.get(session_id)

            if not auth_ctx and config.require_auth:
                response = JSONResponse(
                    {"error": "Unauthorized", "detail": "Missing Authorization header or invalid session."},
                    status_code=401,
                    headers=auth_challenge_headers,
                )
                await response(scope, receive, send)
                return

            token_reset = current_auth.set(auth_ctx)
            try:
                await self.app(scope, receive, send)
            finally:
                current_auth.reset(token_reset)
            return

        # 5. Streamable HTTP Transport (config.mcp_path, default /mcp)
        if path.startswith(config.mcp_path):
            token = extract_token_from_request(request)
            auth_ctx = create_auth_context_from_token(token) if token else None

            if not auth_ctx and config.require_auth:
                response = JSONResponse(
                    {"error": "Unauthorized", "detail": "Missing or invalid Bearer authentication token for Streamable HTTP."},
                    status_code=401,
                    headers=auth_challenge_headers,
                )
                await response(scope, receive, send)
                return

            token_reset = current_auth.set(auth_ctx)
            try:
                await self.app(scope, receive, send)
            finally:
                current_auth.reset(token_reset)
            return

        # Fallthrough for other requests
        await self.app(scope, receive, send)
