"""CRM and Custom Record tools for Valstorm MCP Server delegating to valstorm-client SDK."""

import json
from typing import Any

import valstorm_mcp.proxy as proxy_module
from valstorm.exceptions import ValstormHttpError
from valstorm_mcp.auth import require_scope
from valstorm_mcp.proxy import ProxyClientError


async def record_query_sql(
    query: str,
    bypass_cache: bool = False,
    limit: int = 50,
) -> str:
    """Executes a custom Valstorm SQL query against workspace records and schemas.

    Supports custom keywords (ME, PHONE:, dynamic dates like today, this_month, last_n_days:7).

    Args:
        query: The SQL query (e.g. "SELECT id, name, email FROM contact WHERE name LIKE 'John%' LIMIT 10").
        bypass_cache: Set to True to bypass Redis query cache and fetch live records.
        limit: Suggested result limit (if not explicitly specified in SQL string).

    Returns:
        JSON string containing the array of matched records.
    """
    require_scope("query:execute")

    clean_query = query.strip()
    # Add limit if absent and not aggregate
    if "LIMIT" not in clean_query.upper() and "COUNT(" not in clean_query.upper():
        clean_query = f"{clean_query} LIMIT {limit}"

    payload = {
        "query": clean_query,
        "bypass_cache": bypass_cache,
    }

    try:
        data = await proxy_module.proxy_client.post("query", json_data=payload)
        return json.dumps(data, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to execute SQL query: {e!s}"})


async def record_get(api_name: str, record_id: str) -> str:
    """Retrieves a single record by its ID and object collection/schema name.

    Args:
        api_name: The API name of the object (e.g. 'contact', 'account', 'deal', 'task', 'custom_obj__c').
        record_id: The unique ID of the record (e.g. 'cont_12345678').

    Returns:
        JSON string of the record data.
    """
    require_scope("record:read")

    clean_name = api_name.strip()
    clean_id = record_id.strip()
    query = f"SELECT * FROM {clean_name} WHERE id = '{clean_id}' LIMIT 1"

    payload = {
        "query": query,
        "bypass_cache": True,
    }

    try:
        data = await proxy_module.proxy_client.post("query", json_data=payload)
        records = data if isinstance(data, list) else data.get("records", [])
        if not records:
            return json.dumps({"error": f"Record '{clean_id}' not found in '{clean_name}'.", "status_code": 404})
        return json.dumps(records[0], indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to retrieve record '{clean_id}': {e!s}"})


async def record_create(api_name: str, record: dict[str, Any] | list[dict[str, Any]]) -> str:
    """Creates one or multiple records in a standard or custom object collection.

    Args:
        api_name: The API name of the object schema (e.g. 'contact', 'deal', 'task').
        record: Dictionary or list of dictionaries containing the fields to create.

    Returns:
        JSON string of the created record(s) with generated IDs.
    """
    require_scope("record:write")

    clean_name = api_name.strip()
    try:
        data = await proxy_module.proxy_client.post(f"object/{clean_name}", json_data=record)
        return json.dumps(data, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to create record in '{clean_name}': {e!s}"})


async def record_update(api_name: str, record_id: str, data: dict[str, Any]) -> str:
    """Updates/patches fields on an existing record.

    Args:
        api_name: The API name of the object schema (e.g. 'contact', 'deal').
        record_id: The unique ID of the record to update.
        data: Dictionary of field names and their new values.

    Returns:
        JSON string of the updated record result.
    """
    require_scope("record:write")

    clean_name = api_name.strip()
    update_payload = dict(data)
    update_payload["id"] = record_id.strip()

    try:
        result = await proxy_module.proxy_client.patch(f"object/{clean_name}", json_data=update_payload)
        return json.dumps(result, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to update record '{record_id}' in '{clean_name}': {e!s}"})


async def record_delete(api_name: str, record_id: str, confirm: bool = False) -> str:
    """Deletes a record from an object collection.

    Requires explicit confirmation (`confirm=True`) to prevent accidental record removal.

    Args:
        api_name: The API name of the object schema (e.g. 'contact', 'deal').
        record_id: The unique ID of the record to delete.
        confirm: Must be set to True to execute deletion.

    Returns:
        Confirmation of deletion or safety prompt.
    """
    require_scope("record:delete")

    if not confirm:
        return json.dumps({
            "status": "confirmation_required",
            "warning": f"Potentially destructive action: Deleting record '{record_id}' from '{api_name}'. Set 'confirm=True' to proceed.",
            "api_name": api_name,
            "record_id": record_id,
        }, indent=2)

    clean_name = api_name.strip()
    clean_id = record_id.strip()

    try:
        result = await proxy_module.proxy_client.delete(f"object/{clean_name}/{clean_id}")
        return json.dumps({"status": "deleted", "api_name": clean_name, "record_id": clean_id, "result": result}, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to delete record '{clean_id}' in '{clean_name}': {e!s}"})


async def record_query_mongo(collection: str, pipeline: list[dict[str, Any]]) -> str:
    """Executes a structured document aggregation pipeline query.

    Ideal for complex data transformations, lookups, group-by metrics, and nested array queries.

    Args:
        collection: The target collection/table name (e.g. 'contact', 'deal', 'audit_log').
        pipeline: List of document aggregation pipeline stages.

    Returns:
        JSON string containing the array of aggregation results.
    """
    require_scope("query:execute")

    clean_collection = collection.strip()
    payload = {
        "collection": clean_collection,
        "pipeline": pipeline,
    }

    try:
        data = await proxy_module.proxy_client.post("query/mongo", json_data=payload)
        return json.dumps(data, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to execute aggregation query on '{clean_collection}': {e!s}"})


async def record_hydrate_batch(ids: list[str]) -> str:
    """Resolves a list of record IDs across different schemas into display names and schema types in a single batch call.

    Prevents N+1 query waterfalls when resolving lookup relationships and foreign keys.

    Args:
        ids: Array of record IDs (e.g. ['cont_123', 'deal_456', 'vaul_789']).

    Returns:
        JSON object mapping each ID to its hydrated name, schema, and metadata.
    """
    require_scope("record:read")

    clean_ids = [str(i).strip() for i in ids if str(i).strip()]
    if not clean_ids:
        return json.dumps({})

    payload = {"ids": clean_ids}

    try:
        data = await proxy_module.proxy_client.post("object/hydrate-batch", json_data=payload)
        return json.dumps(data, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to hydrate batch IDs: {e!s}"})


async def schema_inspect(api_name: str | None = None) -> str:
    """Discovers object definitions, available fields, relationship lookups, and validation rules in the workspace.

    Args:
        api_name: Optional object API name to inspect (e.g. 'contact', 'task', 'deal'). If omitted, returns all schemas.

    Returns:
        JSON string of the schema definition or organization schemas map.
    """
    require_scope("record:read")

    try:
        if api_name and api_name.strip():
            clean_name = api_name.strip()
            data = await proxy_module.proxy_client.schemas.get(clean_name)
        else:
            data = await proxy_module.proxy_client.schemas.list()

        return json.dumps(data, indent=2)
    except (ValstormHttpError, ProxyClientError) as e:
        status_code = getattr(e, "status_code", 500)
        return json.dumps({"error": str(e), "status_code": status_code})
    except Exception as e:
        return json.dumps({"error": f"Failed to inspect schema: {e!s}"})
