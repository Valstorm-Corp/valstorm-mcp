from mcp.server.fastmcp import FastMCP
import httpx
import sys
import json
import os
from pathlib import Path
from typing import Optional, Any

# Initialize FastMCP server
mcp = FastMCP("valstorm_mcp")

# Configuration
ENVIRONMENTS = {
    "prod": "https://api.valstorm.com",
    "dev": "https://api-dev.valstorm.com",
    "local": "http://localhost:8010"
}

VALSTORM_ENV = os.environ.get("VALSTORM_ENV", "local").lower()
VALSTORM_PROFILE = os.environ.get("VALSTORM_PROFILE", "default").lower()
BASE_URL = ENVIRONMENTS.get(VALSTORM_ENV, ENVIRONMENTS["prod"])
API_BASE_URL = f"{BASE_URL}/v1"

def get_auth_file(env: str, profile: str) -> Path:
    """Helper to get the auth file path for a specific environment and profile."""
    auth_dir = Path.home() / ".valstorm"
    
    # 1. Try the new standard pattern: auth_{env}_{profile}.json
    new_path = auth_dir / f"auth_{env}_{profile}.json"
    if new_path.exists():
        return new_path
    
    # 2. Fallback for legacy pattern if profile is 'default': auth_{env}.json
    if profile == "default":
        legacy_path = auth_dir / f"auth_{env}.json"
        if legacy_path.exists():
            return legacy_path
            
    # 3. Default to the new pattern for new files
    return new_path

class ValstormAuth:
    def __init__(self, profile: str = VALSTORM_PROFILE):
        self.profile = profile
        self.access_token = None
        self.refresh_token = None
        self.organization_name = None
        self.default_app_id = None
        self._load_tokens()

    @property
    def auth_file(self) -> Path:
        return get_auth_file(VALSTORM_ENV, self.profile)

    def _load_tokens(self):
        # Reset current tokens before loading
        self.access_token = None
        self.refresh_token = None
        self.organization_name = None
        self.default_app_id = None
        
        if self.auth_file.exists():
            try:
                data = json.loads(self.auth_file.read_text())
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.organization_name = data.get("organization_name")
                self.default_app_id = data.get("default_app_id")
            except Exception as e:
                print(f"Error loading tokens for profile {self.profile}: {e}", file=sys.stderr)

    def _save_tokens(self):
        try:
            self.auth_file.parent.mkdir(parents=True, exist_ok=True)
            self.auth_file.write_text(json.dumps({
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "organization_name": self.organization_name,
                "default_app_id": self.default_app_id
            }))
        except Exception as e:
            print(f"Error saving tokens for profile {self.profile}: {e}", file=sys.stderr)

    async def get_client(self) -> httpx.AsyncClient:
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        
        client = httpx.AsyncClient(base_url=API_BASE_URL, headers=headers)
        return client

    async def refresh_auth(self) -> bool:
        if not self.refresh_token:
            return False
        
        try:
            async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
                response = await client.post("/oauth2/refresh", json={"refresh_token": self.refresh_token})
                if response.status_code == 200:
                    data = response.json()
                    self.access_token = data.get("access_token")
                    # Some refresh endpoints might return a new refresh token too
                    if "refresh_token" in data:
                        self.refresh_token = data.get("refresh_token")
                    self._save_tokens()
                    return True
                else:
                    print(f"Refresh failed: {response.status_code} {response.text}", file=sys.stderr)
                    return False
        except Exception as e:
            print(f"Error refreshing token: {e}", file=sys.stderr)
            return False

    async def get_default_app_id(self) -> Optional[str]:
        if self.default_app_id:
            return self.default_app_id
        
        # Load user data to get organization_name
        if not self.organization_name:
            client = await self.get_client()
            try:
                response = await client.get("/auth/load")
                if response.status_code == 401:
                    if await self.refresh_auth():
                        client = await self.get_client()
                        response = await client.get("/auth/load")
                
                if response.status_code == 200:
                    user_data = response.json()
                    self.organization_name = user_data.get("organization_name")
                    self._save_tokens()
            finally:
                await client.aclose()
        
        # If we have the organization name, query the app ID
        if self.organization_name:
            client = await self.get_client()
            try:
                query = f"SELECT id, name FROM app WHERE name LIKE '{self.organization_name} %' LIMIT 1"
                response = await client.get("/query", params={"q": query, "bypass_cache": "false"})
                if response.status_code == 401:
                    if await self.refresh_auth():
                        client = await self.get_client()
                        response = await client.get("/query", params={"q": query, "bypass_cache": "false"})
                
                if response.status_code == 200:
                    data = response.json()
                    # The query result is typically namespaced by the object, e.g., {"app": [{"id": "...", ...}]}
                    for key, records in data.items():
                        if isinstance(records, list) and len(records) > 0:
                            self.default_app_id = records[0].get("id")
                            self._save_tokens()
                            break
            finally:
                await client.aclose()
                
        return self.default_app_id

auth_manager = ValstormAuth()

@mcp.tool()
async def create_records(collection_name: str, records: Any) -> str:
    """
    Create one or more records in a specific collection.
    
    'records' can be a single dictionary or a list of dictionaries.
    Automatically checks the schema first to ensure the collection exists.
    """
    # Ensure records is a list for the API if it's not already
    payload = records if isinstance(records, (list, dict)) else None
    if payload is None:
        return "Error: 'records' must be a dictionary or a list of dictionaries."

    # 1. Check schema first
    schema_info = await get_schema(collection_name)
    if "error" in schema_info.lower() or "not found" in schema_info.lower():
        return f"Error: Schema for '{collection_name}' not found. Please create the schema first."

    async def make_request(client):
        return await client.post(f"/object/{collection_name}", json=payload)

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            if await auth_manager.refresh_auth():
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code in [200, 201]:
            return json.dumps(response.json(), indent=2)
        else:
            return f"Failed to create records: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error creating records: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def update_records(collection_name: str, records: Any) -> str:
    """
    Update one or more records in a specific collection.
    
    'records' can be a single dictionary or a list of dictionaries.
    Each record MUST contain an 'id' field.
    Automatically checks the schema first.
    """
    payload = records if isinstance(records, (list, dict)) else None
    if payload is None:
        return "Error: 'records' must be a dictionary or a list of dictionaries."

    # 1. Check schema first
    schema_info = await get_schema(collection_name)
    if "error" in schema_info.lower() or "not found" in schema_info.lower():
        return f"Error: Schema for '{collection_name}' not found."

    async def make_request(client):
        return await client.patch(f"/object/{collection_name}", json=payload)

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            if await auth_manager.refresh_auth():
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code in [200, 204]:
            if response.status_code == 204:
                return "Records updated successfully."
            return json.dumps(response.json(), indent=2)
        else:
            return f"Failed to update records: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error updating records: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def delete_records(collection_name: str, record_ids: Any) -> str:
    """
    Delete one or more records from a specific collection.
    
    'record_ids' can be a single string ID or a list of string IDs.
    """
    ids_list = [record_ids] if isinstance(record_ids, str) else record_ids
    if not isinstance(ids_list, list):
        return "Error: 'record_ids' must be a string or a list of strings."

    # If it's a single ID, we can use the specific DELETE endpoint
    if len(ids_list) == 1:
        record_id = ids_list[0]
        async def make_single_delete(client):
            return await client.delete(f"/object/{collection_name}/{record_id}")
        
        client = await auth_manager.get_client()
        try:
            response = await make_single_delete(client)
            if response.status_code == 401:
                if await auth_manager.refresh_auth():
                    client = await auth_manager.get_client()
                    response = await make_single_delete(client)
            
            if response.status_code in [200, 204]:
                return f"Record {record_id} deleted successfully."
            else:
                return f"Failed to delete record: {response.status_code} {response.text}"
        except Exception as e:
            return f"Error deleting record: {str(e)}"
        finally:
            await client.aclose()
    else:
        # For multiple IDs, we use the bulk delete endpoint with query params
        ids_param = ",".join(ids_list)
        async def make_bulk_delete(client):
            return await client.delete(f"/object/{collection_name}", params={"ids": ids_param})
        
        client = await auth_manager.get_client()
        try:
            response = await make_bulk_delete(client)
            if response.status_code == 401:
                if await auth_manager.refresh_auth():
                    client = await auth_manager.get_client()
                    response = await make_bulk_delete(client)
            
            if response.status_code in [200, 204]:
                return f"Records deleted successfully."
            else:
                return f"Failed to bulk delete records: {response.status_code} {response.text}"
        except Exception as e:
            return f"Error bulk deleting records: {str(e)}"
        finally:
            await client.aclose()

@mcp.tool()
async def oauth_authorize(
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    state: Optional[str] = None,
    scope: Optional[str] = None,
    code_challenge: Optional[str] = None
) -> str:
    """
    Initiate OAuth2 authorization flow.
    Returns a redirect URL for user login.
    """
    payload = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": response_type,
        "state": state,
        "scope": scope,
        "code_challenge": code_challenge
    }
    try:
        async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
            response = await client.post("/oauth2/authorize", json=payload)
            if response.status_code == 200:
                return json.dumps(response.json(), indent=2)
            else:
                return f"Authorization failed: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error during authorization: {str(e)}"

@mcp.tool()
async def oauth_get_code(
    client_id: str,
    state: Optional[str] = None
) -> str:
    """
    Get an OAuth2 authorization code. 
    Requires being already logged in (auth token must be valid).
    """
    payload = {
        "client_id": client_id,
        "state": state
    }
    async def make_request(client):
        return await client.post("/oauth2/code", json=payload)

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
            return f"Failed to get code: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error getting code: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def oauth_get_token(
    client_id: str,
    client_secret: str,
    grant_type: str,
    code: str,
    redirect_uri: str,
    run_as: Optional[str] = None
) -> str:
    """
    Exchange an authorization code or server credentials for access/refresh tokens.
    'grant_type' should be 'authorization_code' or 'server_credentials'.
    'run_as' is required for 'server_credentials'.
    """
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": grant_type,
        "code": code,
        "redirect_uri": redirect_uri,
        "run_as": run_as
    }
    try:
        async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
            response = await client.post("/oauth2/token", json=payload)
            if response.status_code == 200:
                data = response.json()
                auth_manager.access_token = data.get("access_token")
                auth_manager.refresh_token = data.get("refresh_token")
                auth_manager._save_tokens()
                return json.dumps(data, indent=2)
            else:
                return f"Token exchange failed: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error exchanging token: {str(e)}"

@mcp.tool()
async def oauth_login_server(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    run_as: str
) -> str:
    """
    Convenience tool for 'server_credentials' (service account) login.
    Does not require a browser or 2FA.
    """
    return await oauth_get_token(
        client_id=client_id,
        client_secret=client_secret,
        grant_type="server_credentials",
        code="n/a",
        redirect_uri=redirect_uri,
        run_as=run_as
    )

@mcp.tool()
async def login(email: str, password: str) -> str:
    """
    Login to Valstorm. This will trigger a 2FA code sent to your email.
    After calling this, use verify_2fa(email, code) to complete login.
    """
    try:
        async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
            # The API uses OAuth2PasswordRequestForm which is usually form-encoded
            response = await client.post("/oauth2/login", data={
                "username": email,
                "password": password,
                "grant_type": "password"
            })
            
            if response.status_code == 200:
                return "Login initiated. Please check your email for a 2FA code and use the verify_2fa tool."
            else:
                return f"Login failed: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error during login: {str(e)}"

@mcp.tool()
async def verify_2fa(email: str, code: str) -> str:
    """
    Complete Valstorm login using the 2FA code sent to your email.
    """
    try:
        async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
            response = await client.post("/oauth2/verify-2fa", json={
                "email": email,
                "code": code
            })
            
            if response.status_code == 200:
                data = response.json()
                auth_manager.access_token = data.get("access_token")
                auth_manager.refresh_token = data.get("refresh_token")
                auth_manager._save_tokens()
                return "Login successful! Auth tokens saved and will be reused for future requests."
            else:
                return f"Verification failed: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error during 2FA verification: {str(e)}"

@mcp.tool()
async def refresh_auth() -> str:
    """
    Manually refresh the Valstorm authentication token.
    """
    success = await auth_manager.refresh_auth()
    if success:
        return "Authentication token refreshed successfully."
    else:
        return "Failed to refresh authentication token. You may need to login again."

@mcp.tool()
async def get_me() -> str:
    """
    Get the current authenticated user details from Valstorm.
    Tests if the stored authentication is valid.
    """
    async def make_request(client):
        return await client.get("/auth/load")

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            # Try to refresh once
            if await auth_manager.refresh_auth():
                # Re-get client with new token
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        else:
            return f"Failed to get user info: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error getting user info: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def list_schemas() -> str:
    """
    Get a list of all schema definitions available in the organization.
    Useful for discovering available objects and their fields.
    """
    async def make_request(client):
        return await client.get("/schemas")

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
            return f"Failed to list schemas: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error listing schemas: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def get_schema(object_name: str) -> str:
    """
    Get the full schema definition for a specific object (e.g. 'contact', 'lead').
    Includes field names, types, and descriptions.
    """
    async def make_request(client):
        return await client.get(f"/schema/{object_name}")

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
            return f"Failed to get schema for '{object_name}': {response.status_code} {response.text}"
    except Exception as e:
        return f"Error getting schema for '{object_name}': {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def run_sql_query(query: str, bypass_cache: bool = False) -> str:
    """
    Execute a SQL-like query against the Valstorm database.
    
    Supports:
    - SELECT [fields] FROM [object] WHERE [conditions]
    - JOIN ... ON ...
    - Special Keywords:
        - ME: Filters for records owned by the current user (e.g. WHERE owner = ME)
        - PHONE:: Searches across all phone fields (e.g. WHERE PHONE: LIKE '%123%')
    - Dynamic Dates: today, yesterday, this_week, last_month, etc.
    """
    async def make_request(client):
        return await client.get("/query", params={"q": query, "bypass_cache": str(bypass_cache).lower()})

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
            return f"Query failed: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error executing query: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def create_schema(
    name: str,
    app: Optional[str] = None,
    description: Optional[str] = None,
    ownership: bool = True,
    exclusive_ownership: bool = False,
    junction_object: bool = False,
    relates_to_any_object: bool = False
) -> str:
    """
    Create a new schema (object) definition in Valstorm.
    """
    if not app:
        app = await auth_manager.get_default_app_id()
        if not app:
            return "Failed to determine default app ID. Please provide the 'app' parameter explicitly."

    async def make_request(client):
        return await client.post("/schema", json={
            "name": name,
            "description": description,
            "app": app,
            "ownership": ownership,
            "exclusive_ownership": exclusive_ownership,
            "junction_object": junction_object,
            "relates_to_any_object": relates_to_any_object
        })

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            if await auth_manager.refresh_auth():
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code in [200, 201]:
            return json.dumps(response.json(), indent=2)
        else:
            return f"Failed to create schema: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error creating schema: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def update_schema(
    id: str,
    name: str,
    app: Optional[str] = None,
    description: Optional[str] = None,
    ownership: bool = True,
    exclusive_ownership: bool = False,
    junction_object: bool = False,
    relates_to_any_object: bool = False,
    icon: Optional[str] = None,
    color: Optional[str] = None
) -> str:
    """
    Update an existing schema definition.
    """
    if not app:
        app = await auth_manager.get_default_app_id()
        if not app:
            return "Failed to determine default app ID. Please provide the 'app' parameter explicitly."

    async def make_request(client):
        return await client.patch("/schema", json={
            "id": id,
            "name": name,
            "description": description,
            "app": app,
            "ownership": ownership,
            "exclusive_ownership": exclusive_ownership,
            "junction_object": junction_object,
            "relates_to_any_object": relates_to_any_object,
            "icon": icon,
            "color": color
        })

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            if await auth_manager.refresh_auth():
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code in [200, 204]:
            # PATCH might return 204 or the updated object
            if response.status_code == 204:
                return "Schema updated successfully."
            return json.dumps(response.json(), indent=2)
        else:
            return f"Failed to update schema: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error updating schema: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def create_field(
    object_id: str,
    name: str,
    api_name: str,
    type: str,
    app: Optional[str] = None,
    description: Optional[str] = "",
    help_text: Optional[str] = "",
    format: Optional[str] = None,
    required: bool = False,
    default: Optional[Any] = None,
    sensitive: bool = False,
    encrypted: bool = False,
    schema: Optional[str] = None,
    values: Optional[list] = None,
    is_global: bool = False,
    global_list_name: Optional[str] = None,
    object: Optional[str] = None,
    restricted: bool = False,
    pii: bool = False,
    phi: bool = False,
    plural_name: Optional[str] = ""
) -> str:
    """
    Create a new field within a schema.
    
    - type: string, number, boolean, date, datetime, email, phone, picklist, multi-select, lookup, etc.
    - format: specialized formatting (e.g. 'currency', 'percent', 'text-area')
    - values: list of options for enum/picklist types
    """
    if not app:
        app = await auth_manager.get_default_app_id()
        if not app:
            return "Failed to determine default app ID. Please provide the 'app' parameter explicitly."

    async def make_request(client):
        payload = {
            "object_id": object_id,
            "name": name,
            "api_name": api_name,
            "description": description,
            "help_text": help_text,
            "type": type,
            "format": format,
            "required": required,
            "default": default,
            "sensitive": sensitive,
            "encrypted": encrypted,
            "app": app,
            "schema": schema,
            "values": values,
            "global": is_global,
            "global_list_name": global_list_name,
            "object": object,
            "restricted": restricted,
            "pii": pii,
            "phi": phi,
            "plural_name": plural_name
        }
        return await client.post("/schema/field", json=payload)

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            if await auth_manager.refresh_auth():
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code in [200, 201]:
            return json.dumps(response.json(), indent=2)
        else:
            return f"Failed to create field: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error creating field: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def update_field(
    object_id: str,
    name: str,
    api_name: str,
    type: str,
    app: Optional[str] = None,
    description: Optional[str] = "",
    help_text: Optional[str] = "",
    format: Optional[str] = None,
    required: bool = False,
    default: Optional[Any] = None,
    sensitive: bool = False,
    encrypted: bool = False,
    schema: Optional[str] = None,
    values: Optional[list] = None,
    is_global: bool = False,
    global_list_name: Optional[str] = None,
    object: Optional[str] = None,
    restricted: bool = False,
    pii: bool = False,
    phi: bool = False,
    plural_name: Optional[str] = ""
) -> str:
    """
    Update an existing field within a schema.
    """
    if not app:
        app = await auth_manager.get_default_app_id()
        if not app:
            return "Failed to determine default app ID. Please provide the 'app' parameter explicitly."

    async def make_request(client):
        payload = {
            "object_id": object_id,
            "name": name,
            "api_name": api_name,
            "description": description,
            "help_text": help_text,
            "type": type,
            "format": format,
            "required": required,
            "default": default,
            "sensitive": sensitive,
            "encrypted": encrypted,
            "app": app,
            "schema": schema,
            "values": values,
            "global": is_global,
            "global_list_name": global_list_name,
            "object": object,
            "restricted": restricted,
            "pii": pii,
            "phi": phi,
            "plural_name": plural_name
        }
        return await client.patch("/schema/field", json=payload)

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            if await auth_manager.refresh_auth():
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code in [200, 204]:
            if response.status_code == 204:
                return "Field updated successfully."
            return json.dumps(response.json(), indent=2)
        else:
            return f"Failed to update field: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error updating field: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def delete_field(object_id: str, field_name: str) -> str:
    """
    Delete a field from a schema.
    """
    async def make_request(client):
        return await client.delete(f"/schema/{object_id}/{field_name}")

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            if await auth_manager.refresh_auth():
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code in [200, 204]:
            return "Field deleted successfully."
        else:
            return f"Failed to delete field: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error deleting field: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def delete_schema(schema_id: str) -> str:
    """
    Delete an entire schema and its associated data.
    """
    async def make_request(client):
        return await client.delete(f"/schema/{schema_id}")

    client = await auth_manager.get_client()
    try:
        response = await make_request(client)
        
        if response.status_code == 401:
            if await auth_manager.refresh_auth():
                client = await auth_manager.get_client()
                response = await make_request(client)
        
        if response.status_code in [200, 204]:
            return "Schema deleted successfully."
        else:
            return f"Failed to delete schema: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error deleting schema: {str(e)}"
    finally:
        await client.aclose()

@mcp.tool()
async def scaffold_valstorm_object(
    name: str,
    fields: list[dict[str, Any]],
    app: Optional[str] = None,
    description: Optional[str] = None,
    generate_permissions: bool = True
) -> str:
    """
    Scaffold a complete Valstorm object: schema, fields, and standard permissions.
    
    'fields' should be a list of dictionaries with field properties (name, api_name, type, etc.)
    """
    results = []
    
    # 1. Resolve App ID
    if not app:
        app = await auth_manager.get_default_app_id()
        if not app:
            return "Error: Could not determine default app ID."

    # 2. Create Schema
    schema_res_json = await create_schema(name=name, app=app, description=description)
    try:
        schema_res = json.loads(schema_res_json)
        object_id = schema_res.get("id")
        api_name = schema_res.get("api_name")
        results.append(f"Successfully created schema '{name}' (ID: {object_id})")
    except Exception:
        return f"Failed at schema creation step: {schema_res_json}"

    # 3. Create Fields
    field_results = []
    for field_def in fields:
        # Inject object_id and app if missing
        field_def["object_id"] = object_id
        if "app" not in field_def:
            field_def["app"] = app
        
        # We rename 'is_global' if present in the dict to 'global' for the API, 
        # but our create_field tool handles that if called directly.
        # Here we just call the logic.
        res = await create_field(**field_def)
        if "Failed" in res or "Error" in res:
            field_results.append(f"Field '{field_def.get('name')}' failed: {res}")
        else:
            field_results.append(f"Field '{field_def.get('name')}' created.")
    
    results.append("\n".join(field_results))

    # 4. Generate Standard Permissions
    if generate_permissions:
        perm_payload = [
            {
                "name": f"{name} Viewer",
                "object_permissions": {api_name: {"create": False, "read": True, "update": False, "delete": False}},
                "object_field_permissions": {api_name: {"all_fields": {"read": True, "update": False}}},
                "app": {"id": app} # App object usually needs id/name but id is often enough
            },
            {
                "name": f"{name} Editor",
                "object_permissions": {api_name: {"create": True, "read": True, "update": True, "delete": False}},
                "object_field_permissions": {api_name: {"all_fields": {"read": True, "update": True}}},
                "app": {"id": app}
            },
            {
                "name": f"{name} Admin",
                "object_permissions": {api_name: {"create": True, "read": True, "update": True, "delete": True}},
                "object_field_permissions": {api_name: {"all_fields": {"read": True, "update": True}}},
                "app": {"id": app}
            }
        ]
        perm_res = await create_records("permission", perm_payload)
        if "Failed" in perm_res or "Error" in perm_res:
            results.append(f"Permission generation failed: {perm_res}")
        else:
            results.append("Standard Viewer, Editor, and Admin permissions created.")

    return "\n---\n".join(results)

@mcp.tool()
async def switch_account(profile: str) -> str:
    """
    Switch to a different account profile.
    If the profile doesn't exist, you'll need to login.
    """
    auth_manager.profile = profile.lower()
    auth_manager._load_tokens()
    return f"Switched to profile: {auth_manager.profile}. Use 'get_me' to check authentication status."

@mcp.tool()
async def list_accounts() -> str:
    """
    List all available account profiles for the current environment.
    """
    auth_dir = Path.home() / ".valstorm"
    if not auth_dir.exists():
        return "No account profiles found."
    
    profiles = set()
    prefix = f"auth_{VALSTORM_ENV}"
    
    try:
        for f in auth_dir.iterdir():
            if f.is_file() and f.name.startswith(prefix) and f.suffix == ".json":
                name = f.stem
                if name == prefix:
                    profiles.add("default")
                elif name.startswith(f"{prefix}_"):
                    profile = name[len(prefix)+1:]
                    if profile:
                        profiles.add(profile)
    except Exception as e:
        return f"Error listing profiles: {str(e)}"
    
    if not profiles:
        return f"No account profiles found for environment: {VALSTORM_ENV}"
    
    return json.dumps({
        "current_profile": auth_manager.profile,
        "available_profiles": sorted(list(profiles))
    }, indent=2)

@mcp.tool()
async def get_environment() -> str:
    """Gets the current Valstorm environment and profile the MCP is configured to use."""
    return json.dumps({
        "environment": VALSTORM_ENV,
        "profile": auth_manager.profile,
        "base_url": BASE_URL,
        "api_base_url": API_BASE_URL,
        "auth_file": str(auth_manager.auth_file)
    }, indent=2)

@mcp.tool()
async def logout() -> str:
    """
    Logout from the current Valstorm account profile and clear local tokens.
    """
    auth_manager.access_token = None
    auth_manager.refresh_token = None
    if auth_manager.auth_file.exists():
        try:
            auth_manager.auth_file.unlink()
            return f"Successfully logged out of profile '{auth_manager.profile}' and cleared local tokens."
        except Exception as e:
            return f"Error deleting auth file: {str(e)}"
    return f"Already logged out of profile '{auth_manager.profile}'."

@mcp.tool()
async def get_status() -> str:
    """Get the project's operational status."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_BASE_URL}/status")
            response.raise_for_status()
            return response.text
    except Exception as e:
        return f"Error checking status: {str(e)}"

def main():
    """
    Initializes and runs the MCP server with stdio transport.
    This server is intended to be launched by an MCP client.
    """
    # Using print with stderr to avoid corrupting the JSON-RPC stream on stdout
    print("Starting Valstorm MCP server with stdio transport...", file=sys.stderr)
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()

