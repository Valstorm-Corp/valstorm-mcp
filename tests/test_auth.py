"""Unit tests for authentication, cryptographic verification, and scope guardrails."""

import time

import jwt
import pytest

from valstorm_mcp.auth import (
    AuthContext,
    create_auth_context_from_token,
    current_auth,
    get_active_auth_context,
    require_scope,
)
from valstorm_mcp.config import config


def _make_valid_jwt(payload: dict, secret: str = config.jwt_secret) -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


def test_auth_context_scope_matching():
    ctx = AuthContext(
        token="tok1",
        scopes={"vfs:read", "record:read"},
        is_admin=False,
    )
    assert ctx.has_scope("vfs:read") is True
    assert ctx.has_scope("record:read") is True
    assert ctx.has_scope("record:write") is False
    assert ctx.has_scope("vfs:delete") is False


def test_auth_context_wildcard_scope():
    ctx = AuthContext(
        token="tok2",
        scopes={"vfs:*"},
        is_admin=False,
    )
    assert ctx.has_scope("vfs:read") is True
    assert ctx.has_scope("vfs:write") is True
    assert ctx.has_scope("vfs:delete") is True
    assert ctx.has_scope("record:read") is False


def test_auth_context_admin_bypass():
    ctx = AuthContext(
        token="tok3",
        scopes=set(),
        is_admin=True,
    )
    assert ctx.has_scope("vfs:delete") is True
    assert ctx.has_scope("record:delete") is True
    assert ctx.has_scope("query:execute") is True


def test_create_auth_context_from_valid_jwt():
    payload = {
        "user_id": "usr_999",
        "email": "agent@valstorm.com",
        "organization_id": "org_abc",
        "scopes": ["vfs:read", "vfs:write"],
        "exp": int(time.time()) + 3600,
    }
    token = _make_valid_jwt(payload)
    ctx = create_auth_context_from_token(token)

    assert ctx is not None
    assert ctx.user_id == "usr_999"
    assert ctx.email == "agent@valstorm.com"
    assert ctx.organization_id == "org_abc"
    assert "vfs:read" in ctx.scopes
    assert "vfs:write" in ctx.scopes
    assert ctx.has_scope("vfs:delete") is False


def test_create_auth_context_rejects_forged_signature():
    payload = {
        "user_id": "usr_hacker",
        "is_admin": True,
        "scopes": ["*:*"],
    }
    forged_token = _make_valid_jwt(payload, secret="wrong_secret_key_that_is_32_chars_long!")
    ctx = create_auth_context_from_token(forged_token)
    assert ctx is None


def test_create_auth_context_rejects_expired_jwt():
    payload = {
        "user_id": "usr_999",
        "email": "agent@valstorm.com",
        "exp": int(time.time()) - 100,  # Expired
    }
    expired_token = _make_valid_jwt(payload)
    ctx = create_auth_context_from_token(expired_token)
    assert ctx is None


def test_auth_context_is_expired_flag():
    ctx_active = AuthContext(token="tok", expires_at=time.time() + 3600)
    assert ctx_active.is_expired() is False

    ctx_expired = AuthContext(token="tok", expires_at=time.time() - 10)
    assert ctx_expired.is_expired() is True
    assert ctx_expired.has_scope("vfs:read") is False


def test_no_fallback_token_in_active_context(monkeypatch):
    monkeypatch.setenv("VALSTORM_TOKEN", "should_never_be_used_as_fallback")
    current_auth.set(None)
    ctx = get_active_auth_context()
    assert ctx is None


def test_require_scope_enforcement():
    ctx = AuthContext(token="tok4", scopes={"vfs:read"})
    token_reset = current_auth.set(ctx)

    try:
        # Should pass
        require_scope("vfs:read")

        # Should raise
        with pytest.raises(PermissionError) as exc_info:
            require_scope("vfs:delete")
        assert "Missing required scope 'vfs:delete'" in str(exc_info.value)
    finally:
        current_auth.reset(token_reset)
