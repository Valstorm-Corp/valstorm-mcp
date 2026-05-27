# Valstorm MCP Server — Claude Guide

FastMCP server that exposes Valstorm API operations as tools for LLMs (Claude, Gemini, etc.) via the Model Context Protocol.

## Key Files

```
apps/valstorm-mcp/
  src/valstorm_mcp/
    main.py        # All tool definitions + ValstormAuth class + FastMCP setup
    __init__.py
  pyproject.toml   # Dependencies: mcp[cli]>=1.26.0, httpx>=0.28.1
  README.md        # Client connection setup
  docs.md          # MCP protocol docs
```

## Running

```bash
# Install deps
cd apps/valstorm-mcp
uv sync

# Run directly (for testing)
uv run python -m valstorm_mcp.main

# Or via the entry point defined in pyproject.toml
uv run valstorm-mcp
```

## Configuration (env vars + valstorm.json)

Two sources, in priority order:

1. **`valstorm.json`** in the cwd (or any parent directory) — the CLI's project config.
   The MCP walks up from cwd at startup to find this file and uses its `env` / `profile`.
   The MCP also **re-reads it on every authenticated tool call** (mtime-gated), so an
   external `valstorm auth switch <profile> --env <env>` is reflected in the running
   server without restarting it.

2. **Env vars** (fallback when there is no `valstorm.json`):
   ```bash
   VALSTORM_ENV=local        # prod | dev | local (default: local)
   VALSTORM_PROFILE=default  # profile name (default: default)
   ```

Environments:
- `prod` → `https://api.valstorm.com`
- `dev` → `https://api-dev.valstorm.com`
- `local` → `http://localhost:8010`

## Authentication

The MCP server does **not** handle its own login. It reads tokens from the same files the CLI writes:

```
~/.valstorm/auth_{env}_{profile}.json
~/.valstorm/auth_{env}.json  (legacy fallback for "default" profile)
```

Auth file format:
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "organization_name": "...",
  "default_app_id": "..."
}
```

**Flow**: `valstorm login` (CLI) → writes token file → MCP server reads it.

The `ValstormAuth` class in `main.py` handles:
- Auto-reload when token file is modified externally (file mtime check)
- Automatic token refresh on 401 responses via `POST /v1/oauth2/refresh`
- Persisting refreshed tokens back to disk

## Connecting to Claude Code (this tool)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (or your MCP config):

```json
{
  "mcpServers": {
    "valstorm": {
      "command": "uv",
      "args": ["run", "python", "-m", "valstorm_mcp.main"],
      "cwd": "/Users/jared/Documents/Code/monorepo/apps/valstorm-mcp",
      "env": {
        "VALSTORM_ENV": "local",
        "VALSTORM_PROFILE": "default"
      }
    }
  }
}
```

## All Available Tools

### Auth Tools
| Tool | Description |
|------|-------------|
| `login(email, password)` | Password login — triggers 2FA email |
| `verify_2fa(email, code)` | Complete 2FA login, saves tokens |
| `refresh_auth()` | Manually refresh access token |
| `logout()` | Clear tokens for current profile |
| `get_me()` | Get current user info / test auth |
| `get_environment()` | Show current env, profile, base URL, auth file path (auto-reloads valstorm.json first) |
| `switch_account(profile, env=None)` | Switch profile and optionally env. Validates auth file exists; errors clearly otherwise. In-memory only — persist with the CLI's `valstorm auth switch` |
| `list_accounts()` | List all saved profiles for the current env |

### OAuth Tools (for building integrations)
| Tool | Description |
|------|-------------|
| `oauth_authorize(client_id, redirect_uri, ...)` | Start OAuth authorization flow |
| `oauth_get_code(client_id, state)` | Get auth code (requires valid session) |
| `oauth_get_token(client_id, client_secret, grant_type, code, redirect_uri)` | Exchange code for tokens |
| `oauth_login_server(client_id, client_secret, redirect_uri, run_as)` | Service account login (no browser/2FA) |

### Record CRUD
| Tool | Description |
|------|-------------|
| `create_records(collection_name, records)` | Create one or multiple records (`dict` or `list[dict]`) |
| `update_records(collection_name, records)` | Update records — each must have `id` field |
| `delete_records(collection_name, record_ids)` | Delete one (`str`) or many (`list[str]`) records |

### Schema Management
| Tool | Description |
|------|-------------|
| `list_schemas()` | List all schemas/objects in the org |
| `get_schema(object_name)` | Get full field definitions for an object |
| `create_schema(name, app, description, ...)` | Create a new object definition |
| `update_schema(id, name, app, ...)` | Update an existing schema |
| `delete_schema(schema_id)` | Delete a schema |
| `create_field(object_id, name, api_name, type, ...)` | Add a field to a schema |
| `update_field(object_id, name, api_name, type, ...)` | Modify an existing field |
| `delete_field(object_id, field_name)` | Remove a field |

### Query & Status
| Tool | Description |
|------|-------------|
| `run_sql_query(query, bypass_cache)` | Execute SQL-like query (supports ME, PHONE:, dynamic dates) |
| `get_status()` | Check API health |

### Scaffolding
| Tool | Description |
|------|-------------|
| `scaffold_valstorm_object(name, fields, app, description, generate_permissions)` | Full object scaffold: creates schema + all fields + standard Viewer/Editor/Admin permissions in one call |

## Adding New Tools

1. Add an `async` function decorated with `@mcp.tool()` in `main.py`
2. **Write an exhaustive docstring** — the LLM relies entirely on it to understand when/how to use the tool
3. Use the `ValstormAuth` retry pattern:
   ```python
   @mcp.tool()
   async def my_tool(param: str) -> str:
       """Detailed description of what this does and when to use it."""
       async def make_request(client):
           return await client.get(f"/some/endpoint/{param}")

       client = await auth_manager.get_client()
       try:
           response = await make_request(client)
           if response.status_code == 401:
               if await auth_manager.refresh_auth():
                   client = await auth_manager.get_client()
                   response = await make_request(client)

           if response.status_code == 200:
               return json.dumps(response.json(), indent=2)
           else:
               return f"Failed: {response.status_code} {response.text}"
       except Exception as e:
           return f"Error: {str(e)}"
       finally:
           await client.aclose()
   ```
4. **Always return errors as strings** — don't raise exceptions. The LLM reads the error and adjusts.
5. Keep args simple: `str`, `bool`, `dict`, `list`, `Optional[str]`

## Default App ID

Many tools need an `app` parameter. The `ValstormAuth.get_default_app_id()` method auto-discovers it:
1. Calls `GET /v1/auth/load` to get `organization_name`
2. Queries `SELECT id FROM app WHERE name LIKE '{org_name} %' LIMIT 1`
3. Caches the result in the auth JSON file

If auto-discovery fails, pass `app` explicitly to the tool.

## API Endpoints Reference

The server calls `{BASE_URL}/v1/...`:

- `GET /auth/load` — current user
- `GET /schemas` — list all schemas
- `GET /schema/{name}` — get schema
- `POST /schema` — create schema
- `PATCH /schema` — update schema
- `DELETE /schema/{id}` — delete schema
- `POST /schema/field` — create field
- `PATCH /schema/field` — update field
- `DELETE /schema/{object_id}/{field_name}` — delete field
- `GET /query?q=...` — SQL query
- `POST /object/{collection}` — create records
- `PATCH /object/{collection}` — update records
- `DELETE /object/{collection}/{id}` — delete single record
- `DELETE /object/{collection}?ids=...` — bulk delete
- `POST /oauth2/login` — password login
- `POST /oauth2/verify-2fa` — complete 2FA
- `POST /oauth2/refresh` — refresh token
- `POST /oauth2/authorize` — start OAuth flow
- `POST /oauth2/code` — get auth code
- `POST /oauth2/token` — exchange code for tokens
