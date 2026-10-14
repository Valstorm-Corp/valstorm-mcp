"""Unit tests for ServerConfig and environment resolution."""

from valstorm_mcp.config import ServerConfig


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("VALSTORM_ENV", raising=False)
    monkeypatch.delenv("VALSTORM_PROFILE", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("VALSTORM_MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("VALSTORM_BASE_URL", raising=False)
    monkeypatch.delenv("VALSTORM_API_URL", raising=False)

    cfg = ServerConfig()
    assert cfg.port == 8660
    assert cfg.host == "0.0.0.0"
    assert "valstorm.com" in cfg.base_url
    assert cfg.public_url == "https://mcp.valstorm.com"
    assert cfg.resource_url == "https://mcp.valstorm.com/mcp"


def test_config_env_overrides(monkeypatch):
    monkeypatch.delenv("VALSTORM_BASE_URL", raising=False)
    monkeypatch.delenv("VALSTORM_API_URL", raising=False)
    monkeypatch.setenv("VALSTORM_ENV", "dev")
    monkeypatch.setenv("VALSTORM_PROFILE", "custom")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("VALSTORM_MCP_PUBLIC_URL", "https://mcp-dev.valstorm.com")
    monkeypatch.setenv("VALSTORM_MCP_PATH", "/custom-mcp")

    cfg = ServerConfig()
    assert cfg.env == "dev"
    assert cfg.profile == "custom"
    assert cfg.port == 9000
    assert cfg.host == "127.0.0.1"
    assert cfg.base_url == "https://api-dev.valstorm.com"
    assert cfg.api_base_url == "https://api-dev.valstorm.com/v1"
    assert cfg.public_url == "https://mcp-dev.valstorm.com"
    assert cfg.mcp_path == "/custom-mcp"
    assert cfg.resource_url == "https://mcp-dev.valstorm.com/custom-mcp"


def test_config_no_fallback_token_method():
    cfg = ServerConfig()
    assert not hasattr(cfg, "get_fallback_token"), "Fallback token method must not exist"
    assert not hasattr(cfg, "get_local_auth_file"), "Local auth file lookup must not exist"
