# Valstorm Hosted MCP Server (`apps/valstorm-mcp-server`)

Official hosted Model Context Protocol (MCP) server for the Valstorm Enterprise Platform.

- **Official Tool Page & Quickstart:** [https://www.valstorm.com/free-tools/valstorm-mcp](https://www.valstorm.com/free-tools/valstorm-mcp?utm_source=github&utm_medium=readme&utm_campaign=valstorm_mcp)
- **All Valstorm Free & Open-Source Tools:** [https://www.valstorm.com/free-tools](https://www.valstorm.com/free-tools?utm_source=github&utm_medium=readme&utm_campaign=valstorm_mcp)
- **Valstorm BOS Platform Overview:** [https://www.valstorm.com/bos](https://www.valstorm.com/bos?utm_source=github&utm_medium=readme&utm_campaign=valstorm_mcp)

This microservice acts as a stateless, high-performance SSE & JSON-RPC proxy to the Valstorm Core API (`https://api.valstorm.com/v1`), exposing granularly scoped tools for autonomous agents (Claude Desktop, Cursor, hermes profiles, internal enterprise workers) while offloading binary transfer via direct S3 recipes.

## Architecture Highlights

- **Dual Transport:** Serves Streamable HTTP at `/mcp` (the current MCP standard, used by Claude and Cursor), the legacy SSE transport at `/sse` + `/messages` for older clients, and `stdio` for local CLI workflows.
- **Stateless Transport:** `/mcp` runs stateless, so any replica can answer any request. Multi-replica deployments need no session affinity.
- **OAuth 2.1 Discovery:** Publishes RFC 9728 protected-resource and RFC 8414 authorization-server metadata describing itself, delegating authorization and token endpoints to the core API.
- **Stateless Proxy:** Zero direct database attachments. Forwards JSON-RPC tool invocations to the core Valstorm REST API via `httpx.AsyncClient`.
- **Granular Scopes:** Strict token scope inspection (`vfs:read`, `vfs:write`, `vfs:delete`, `record:read`, `record:write`, `record:delete`, `query:execute`).
- **Transfer Orchestrator:** Bypasses JSON-RPC bloat by returning S3 presigned URLs and execution recipes for binary transfers.
- **RAG & Search:** Server-side Gemini RAG (`vfs_ask`) and OpenSearch hybrid retrieval (`vfs_search`).
- **Zero N+1 Querying:** Single-roundtrip document fetching (`vfs_read_text` via `GET /vfs/file/{id}`) and batch foreign key hydration (`record_hydrate_batch` via `POST /object/hydrate-batch`).

## Quickstart

### Running locally with stdio:
```bash
uv run --project apps/valstorm-mcp-server valstorm-mcp --transport stdio
```

### Running the hosted HTTP server (Streamable HTTP + SSE):
```bash
uv run --project apps/valstorm-mcp-server valstorm-mcp --transport streamable-http --host 0.0.0.0 --port 8660
```
Or via uvicorn directly:
```bash
uv run --project apps/valstorm-mcp-server uvicorn valstorm_mcp.server:app --host 0.0.0.0 --port 8660
```

## Available Tool Registry (16 Tools)

### Virtual File System (VFS)
1. `vfs_search(query, limit=10, vault_id=None)` — Hybrid OpenSearch vector & metadata chunk search.
2. `vfs_ask(query, vault_id=None, limit=5)` — Server-side Gemini RAG with synthesized markdown answer and citations.
3. `vfs_read_text(file_id, max_bytes=100000)` — High-performance single-roundtrip text reader (`GET /vfs/file/{id}`).
4. `vfs_browse(target='root', browse_type='vault')` — Explores VFS vaults, folder trees, or full hierarchy snapshots.
5. `vfs_move(item_id, to_vault_id)` — Atomic file or vault folder moves with path cascades.
6. `vfs_prepare_upload(filename, vault_id=None, content_type="application/octet-stream")` — Generates out-of-band upload recipe.
7. `vfs_get_download_url(file_id)` — Generates presigned S3 GET URL & terminal download recipe.
8. `vfs_delete_file(file_id, confirm=False)` — Secure file deletion guarded by confirmation flag.

### Records, SQL, & Schemas
9. `record_query_sql(query, bypass_cache=False, limit=50)` — Executes custom Valstorm SQL engine (`SELECT ... WHERE owner = ME`).
10. `record_query_mongo(collection, pipeline)` — Structured document aggregation pipeline (`$match`, `$group`, `$lookup`, `$facet`).
11. `record_hydrate_batch(ids)` — Resolves foreign key ID arrays to display names and schemas in a single roundtrip.
12. `record_get(api_name, record_id)` — Fetches single record by ID.
13. `record_create(api_name, record)` — Creates single or batch records.
14. `record_update(api_name, record_id, data)` — Patches existing record fields.
15. `record_delete(api_name, record_id, confirm=False)` — Removes a record guarded by confirmation flag.
16. `schema_inspect(api_name=None)` — Introspects object schemas, fields, lookups, and validation rules.

---

## License & Commercial Terms

Valstorm Hosted MCP Server is distributed under a **Dual License** model:

- **Noncommercial Use (Community & Academic)**: Licensed under the **[PolyForm Noncommercial License 1.0.0](LICENSE.md)**. You are free to run, inspect, and evaluate the server for personal, hobby, or educational purposes without fees.
- **Commercial & Enterprise Use**: Any commercial use—including running internal business operations, powering SaaS backends, managed hosting, or client consulting deliverables—requires a commercial agreement.

For commercial licensing, enterprise BYOC terms, or official partner programs:
- **Email:** team@valstorm.com
- **Website:** [Valstorm Pricing](https://valstorm.com/pricing)
- **Enterprise Portal:** [Valstorm Enterprise](https://valstorm.com/enterprise)

See [CONTRIBUTING.md](CONTRIBUTING.md) for community guidelines and development policies.

```
Copyright (c) 2026 Valstorm LLC. All rights reserved.
```

---

<p align="center">
  Built by <a href="https://www.valstorm.com/register?utm_source=github&utm_medium=readme&utm_campaign=valstorm_mcp_server&utm_content=footer_cta"><strong>Valstorm</strong></a> — The everything engine for business operations.
</p>
