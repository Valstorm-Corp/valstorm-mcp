"""Configuration management for Valstorm Hosted MCP Server."""

import json
import os
import sys
from pathlib import Path

ENVIRONMENTS: dict[str, str] = {
    "prod": "https://api.valstorm.com",
    "dev": "https://api-dev.valstorm.com",
    "local": "http://localhost:8010",
    "blue": "https://api-blue.valstorm.com",
    "green": "https://api-green.valstorm.com",
}

# Public origin of THIS MCP server per environment. Distinct from ENVIRONMENTS,
# which addresses the upstream Valstorm API. OAuth discovery documents must
# describe this server, not the API, so the two must never be conflated.
PUBLIC_ORIGINS: dict[str, str] = {
    "prod": "https://mcp.valstorm.com",
    "dev": "https://mcp-dev.valstorm.com",
}

# Scopes this resource server understands. Mirrors DEFAULT_MCP_APPS in the
# core API (apps/api/app/auth/oauth_helpers.py) for the `valstorm_mcp` client.
SUPPORTED_SCOPES = [
    "vfs:read",
    "vfs:write",
    "vfs:delete",
    "record:read",
    "record:write",
    "record:delete",
    "query:execute",
]


def find_project_config() -> Path | None:
    """Walk up from cwd looking for a valstorm.json (the CLI's project config)."""
    cur = Path.cwd()
    while cur != cur.parent:
        candidate = cur / "valstorm.json"
        if candidate.exists():
            return candidate
        cur = cur.parent
    return None


class ServerConfig:
    """Server configuration and runtime environment resolution."""

    def __init__(self):
        self.env: str = os.getenv("VALSTORM_ENV", "prod").lower()
        self.profile: str = os.getenv("VALSTORM_PROFILE", "vdk").lower()
        self.port: int = int(os.getenv("PORT", os.getenv("VALSTORM_MCP_PORT", "8660")))
        self.host: str = os.getenv("HOST", os.getenv("VALSTORM_MCP_HOST", "0.0.0.0"))
        self.default_timeout: float = float(os.getenv("VALSTORM_HTTP_TIMEOUT", "30.0"))
        self.rag_timeout: float = float(os.getenv("VALSTORM_RAG_TIMEOUT", "120.0"))
        self.require_auth: bool = os.getenv("VALSTORM_MCP_REQUIRE_AUTH", "true").lower() in ("true", "1", "yes")
        self.refresh_token: str | None = os.getenv("VALSTORM_REFRESH_TOKEN")

        # Custom override for API Base URL if provided
        custom_base = os.getenv("VALSTORM_BASE_URL")
        if custom_base:
            self._custom_base_url = custom_base.rstrip("/")
        else:
            self._custom_base_url = None

        # Try to resolve from valstorm.json if present
        self._config_path: Path | None = find_project_config()
        self._load_from_project_config()

        # --- Hosted MCP identity (resolved after env is final) ---
        # Path the Streamable HTTP transport is served on. Must match
        # FastMCP's streamable_http_path in server.py.
        self.mcp_path: str = "/" + os.getenv("VALSTORM_MCP_PATH", "/mcp").strip("/")

        # This server's own externally reachable origin.
        self._public_url: str = (
            os.getenv("VALSTORM_MCP_PUBLIC_URL")
            or PUBLIC_ORIGINS.get(self.env)
            or f"http://localhost:{self.port}"
        ).rstrip("/")

        # OAuth client the core API already provisions for MCP access.
        # See DEFAULT_MCP_APPS in apps/api/app/auth/oauth_helpers.py.
        self.oauth_client_id: str = os.getenv("VALSTORM_MCP_OAUTH_CLIENT_ID", "valstorm_mcp")

        # The core API implements no RFC 7591 endpoint. When enabled, this
        # server answers registration requests with the static public client
        # above so that MCP clients requiring DCR can complete discovery.
        self.enable_dcr: bool = os.getenv("VALSTORM_MCP_ENABLE_DCR", "true").lower() in ("true", "1", "yes")

        # JWT Secret for cryptographic signature verification
        self.jwt_secret: str = os.getenv(
            "JWT_SECRET", "test_jwt_secret_key_for_unit_tests_32_bytes_long"
        )

    def _load_from_project_config(self):
        if self._config_path and self._config_path.exists():
            try:
                data = json.loads(self._config_path.read_text())
                if "env" in data and not os.getenv("VALSTORM_ENV"):
                    self.env = str(data["env"]).lower()
                if "profile" in data and not os.getenv("VALSTORM_PROFILE"):
                    self.profile = str(data["profile"]).lower()
            except Exception as e:
                print(f"[config] Notice: Could not parse {self._config_path}: {e}", file=sys.stderr)

    @property
    def base_url(self) -> str:
        """Base URL of the upstream Valstorm REST API."""
        if self._custom_base_url:
            return self._custom_base_url
        return ENVIRONMENTS.get(self.env, ENVIRONMENTS["prod"])

    @property
    def api_base_url(self) -> str:
        return f"{self.base_url}/v1"

    # --- OAuth 2.1 / MCP discovery identities ---

    @property
    def public_url(self) -> str:
        """Externally reachable origin of this MCP server."""
        return self._public_url

    @property
    def resource_url(self) -> str:
        """Canonical URI of this MCP server (RFC 8707 resource identifier)."""
        return f"{self._public_url}{self.mcp_path}"

    @property
    def resource_metadata_url(self) -> str:
        """Path-aware RFC 9728 protected-resource metadata URL."""
        suffix = self.mcp_path.strip("/")
        return f"{self._public_url}/.well-known/oauth-protected-resource" + (f"/{suffix}" if suffix else "")

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.api_base_url}/oauth2/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.api_base_url}/oauth2/token"

    @property
    def registration_endpoint(self) -> str | None:
        return f"{self._public_url}/oauth2/register" if self.enable_dcr else None


config = ServerConfig()
