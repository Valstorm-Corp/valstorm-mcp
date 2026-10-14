"""OAuth 2.1 discovery metadata and client registration for the hosted MCP server.

The MCP authorization spec requires this server, acting as an OAuth 2.0
*resource server*, to publish RFC 9728 protected-resource metadata describing
itself, and requires clients to reach an RFC 8414 authorization-server metadata
document from there.

Two constraints shape what is served here:

1. RFC 8414 requires an authorization server's ``issuer`` to match the origin
   the metadata is served from. The core Valstorm API publishes no metadata
   document of its own, so this server publishes one under its *own* issuer
   while delegating the authorization and token endpoints to the API. This is
   permitted -- endpoints need not share the issuer's origin -- and keeps the
   change contained to this service.

2. The core API implements no RFC 7591 registration endpoint, and its OAuth
   clients live in the ``integrated_app`` collection plus a hardcoded
   ``DEFAULT_MCP_APPS`` table. Clients that require dynamic registration to
   complete discovery are therefore answered by the shim below, which returns
   the already-provisioned public ``valstorm_mcp`` client rather than creating
   anything. See ``config.enable_dcr`` to disable it.
"""

import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from valstorm_mcp.config import SUPPORTED_SCOPES, config

router = APIRouter()


def _web_base_url() -> str:
    return "https://bos-dev.valstorm.com" if config.env == "dev" else "https://bos.valstorm.com"


# Mirrors is_valid_redirect_uri() in apps/api/app/auth/oauth_helpers.py. A
# registration request whose redirect URI the API would refuse is rejected here
# rather than handing back a client id that cannot complete a flow.
ALLOWED_REDIRECT_PREFIXES = (
    "https://claude.ai/",
    "https://staging.claude.ai/",
    "https://cursor.com/",
    "http://localhost:",
    "http://127.0.0.1:",
    "http://[::1]:",
    "claude://",
    "cursor://",
    "vscode://",
)


def _redirect_uri_allowed(uri: str) -> bool:
    if any(uri.startswith(prefix) for prefix in ALLOWED_REDIRECT_PREFIXES):
        return True
    # Permit a loopback callback on any port, which OAuth 2.1 requires for
    # native clients (RFC 8252 section 7.3).
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    return parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")


def protected_resource_metadata() -> dict[str, Any]:
    """RFC 9728 metadata describing this MCP server as a protected resource."""
    return {
        "resource": config.resource_url,
        "authorization_servers": [config.public_url],
        "scopes_supported": SUPPORTED_SCOPES,
        # RFC 6750 permits query-string credentials; the MCP spec forbids them.
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{_web_base_url()}/docs",
    }


def authorization_server_metadata() -> dict[str, Any]:
    """RFC 8414 metadata. Issuer is this origin; endpoints live on the core API."""
    metadata: dict[str, Any] = {
        "issuer": config.public_url,
        "authorization_endpoint": config.authorization_endpoint,
        "token_endpoint": config.token_endpoint,
        "scopes_supported": SUPPORTED_SCOPES,
        "response_types_supported": ["code"],
        # The API's token endpoint implements authorization_code only. Its
        # /oauth2/refresh route is a non-standard shape that no OAuth client
        # can discover, so refresh_token is deliberately not advertised.
        "grant_types_supported": ["authorization_code"],
        # oauth_service.py hardcodes SHA-256 verification; plain is unsupported.
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ],
        "service_documentation": f"{_web_base_url()}/docs",
    }
    if config.registration_endpoint:
        metadata["registration_endpoint"] = config.registration_endpoint
    return metadata


def _assert_known_resource(resource_path: str) -> None:
    """Reject path-aware lookups for resources this server does not serve.

    Answering every suffix would have this server claim to protect resources
    that do not exist here (RFC 9728 section 3.1 ties the suffix to the
    resource it identifies).
    """
    if resource_path.strip("/") not in ("", config.mcp_path.strip("/")):
        raise HTTPException(status_code=404, detail="Unknown protected resource")


# --- Protected resource metadata (RFC 9728) ---
# Served at both the bare path and the path-aware variant. A client addressing
# https://host/mcp derives /.well-known/oauth-protected-resource/mcp; one
# addressing the origin derives the bare path. Both must resolve.


@router.get("/.well-known/oauth-protected-resource", tags=["OAuth"])
async def oauth_protected_resource() -> dict[str, Any]:
    return protected_resource_metadata()


@router.get("/.well-known/oauth-protected-resource/{resource_path:path}", tags=["OAuth"])
async def oauth_protected_resource_path_aware(resource_path: str) -> dict[str, Any]:
    _assert_known_resource(resource_path)
    return protected_resource_metadata()


# --- Authorization server metadata (RFC 8414) ---


@router.get("/.well-known/oauth-authorization-server", tags=["OAuth"])
async def oauth_authorization_server() -> dict[str, Any]:
    return authorization_server_metadata()


@router.get("/.well-known/oauth-authorization-server/{resource_path:path}", tags=["OAuth"])
async def oauth_authorization_server_path_aware(resource_path: str) -> dict[str, Any]:
    _assert_known_resource(resource_path)
    return authorization_server_metadata()


# Some clients probe the OpenID Connect location before the OAuth one.
@router.get("/.well-known/openid-configuration", tags=["OAuth"])
async def openid_configuration() -> dict[str, Any]:
    return authorization_server_metadata()


# --- Client registration shim (RFC 7591) ---


class ClientRegistrationRequest(BaseModel):
    """Subset of RFC 7591 registration metadata this shim inspects."""

    redirect_uris: list[str] = Field(default_factory=list)
    client_name: str | None = None
    scope: str | None = None
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    token_endpoint_auth_method: str | None = None


@router.post("/oauth2/register", tags=["OAuth"], status_code=201)
async def register_client(request: Request) -> JSONResponse:
    """Return the pre-provisioned public MCP client.

    This creates nothing. The core API has no registration endpoint, and
    ``valstorm_mcp`` is already a public client there whose redirect URIs are
    constrained by the API's own allowlist. Returning it lets DCR-dependent
    clients finish discovery without weakening that constraint: the user still
    authenticates and consents at the API's authorization endpoint, and PKCE
    still binds the exchange.
    """
    if not config.enable_dcr:
        return JSONResponse(
            {
                "detail": (
                    "Dynamic client registration is disabled on this server. "
                    f"Configure the client id '{config.oauth_client_id}' manually."
                )
            },
            status_code=404,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    payload = ClientRegistrationRequest.model_validate(body)

    if not payload.redirect_uris:
        return JSONResponse(
            {
                "error": "invalid_redirect_uri",
                "error_description": "At least one redirect_uri is required.",
            },
            status_code=400,
        )

    rejected = [uri for uri in payload.redirect_uris if not _redirect_uri_allowed(uri)]
    if rejected:
        return JSONResponse(
            {
                "error": "invalid_redirect_uri",
                "error_description": (
                    "Redirect URI is not permitted by the Valstorm authorization "
                    f"server: {', '.join(rejected)}"
                ),
            },
            status_code=400,
        )

    granted_scope = payload.scope or " ".join(SUPPORTED_SCOPES)

    return JSONResponse(
        {
            "client_id": config.oauth_client_id,
            "client_id_issued_at": int(time.time()),
            # Public client: no secret is issued, and the API's token endpoint
            # skips secret verification when PKCE is used.
            "token_endpoint_auth_method": "none",
            "redirect_uris": payload.redirect_uris,
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "scope": granted_scope,
            "client_name": payload.client_name or "Valstorm MCP Client",
        },
        status_code=201,
    )
