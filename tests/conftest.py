"""Pytest fixtures and test configuration."""

import pytest

from valstorm_mcp.auth import AuthContext, current_auth


@pytest.fixture(autouse=True)
def reset_auth_and_mcp_state():
    from valstorm_mcp.server import mcp
    token = current_auth.set(None)
    mcp.session_manager._has_started = False
    mcp.session_manager._task_group = None
    yield
    current_auth.reset(token)
    mcp.session_manager._has_started = False
    mcp.session_manager._task_group = None


@pytest.fixture
def mock_auth_context():
    ctx = AuthContext(
        token="mock_jwt_token_12345",
        user_id="user_test123",
        email="test@valstorm.com",
        organization_id="org_test123",
        scopes={
            "vfs:read",
            "vfs:write",
            "vfs:delete",
            "record:read",
            "record:write",
            "record:delete",
            "query:execute",
        },
        is_admin=False,
    )
    token = current_auth.set(ctx)
    yield ctx
    current_auth.reset(token)


@pytest.fixture
def readonly_auth_context():
    ctx = AuthContext(
        token="readonly_jwt_token_12345",
        user_id="user_readonly",
        email="readonly@valstorm.com",
        organization_id="org_test123",
        scopes={"vfs:read", "record:read", "query:execute"},
        is_admin=False,
    )
    token = current_auth.set(ctx)
    yield ctx
    current_auth.reset(token)
