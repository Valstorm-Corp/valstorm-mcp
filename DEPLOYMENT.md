# Hosted Valstorm MCP Server: Deployment & Operations Guide

This guide covers deploying, operating, and verifying the **Hosted Valstorm MCP Server** (`apps/valstorm-mcp-server`) across Development (`dev`) and Production (`prod`) environments.

---

## 1. Architecture & Topology Overview

```text
[External Clients / Agents (Claude Desktop, Cursor, Enterprise Connectors)]
                                │
                                ▼ HTTPS (TLS via Let's Encrypt)
              [NGINX Ingress Controller]
                  ├── mcp-dev.valstorm.com (Dev LoadBalancer)
                  └── mcp.valstorm.com     (Prod LoadBalancer)
                                │
                                ▼ ClusterIP: 8660
            [valstorm-mcp-service (Kubernetes Service)]
                                │
                                ▼
         [valstorm-mcp Pods (FastMCP Streamable HTTP + legacy SSE)]
                                │
                                ▼ Internal VPC REST Calls (httpx Async)
           [Core Valstorm API: https://api.valstorm.com/v1]
```

### Resource Profiles
- **Dev (`k8s/dev/valstorm-mcp-dev.yaml`)**:
  - Namespace: `app`
  - Replicas: `1`
  - Node Pool: `api-2` or `background-workers`
  - Resources: Request `100m CPU / 128Mi RAM`, Limit `1000m CPU / 512Mi RAM`
- **Prod (`k8s/main/valstorm-mcp.yaml`)**:
  - Namespace: `app-green`
  - Replicas: `2` (with hostname `podAntiAffinity` for high availability)
  - Node Pool: `background-workers`, `api-2`, `io-worker-p1`, `io-worker-p2`
  - Resources: Request `100m CPU / 128Mi RAM`, Limit `1000m CPU / 512Mi RAM`

---

## 2. Automated Deployment (GitHub Actions CI/CD)

The repository is configured with automated GitHub Actions workflows that trigger upon code changes in `apps/valstorm-mcp-server/**`:

### Development (`dev` branch)
1. Push or merge changes to `dev` (or `prefixing-integration`).
2. Workflow **`.github/workflows/valstorm-mcp-dev.yaml`** will:
   - Build the Docker container image.
   - Push to `registry.digitalocean.com/canon-connect/valstorm-mcp-server-dev:<git_sha>`.
   - Update `k8s/dev/valstorm-mcp-dev.yaml` with the new image tag.
   - Deploy to the Dev DOKS cluster (`app` namespace).
   - Verify rollout with `kubectl rollout status deployment/valstorm-mcp-dev -n app`.

### Production (`main` branch)
1. Merge pull request to `main`.
2. Workflow **`.github/workflows/valstorm-mcp-prod.yaml`** will:
   - Build the Docker container image.
   - Push to `registry.digitalocean.com/canon-connect/valstorm-mcp-server-prod:<git_sha>`.
   - Update `k8s/main/valstorm-mcp.yaml` with the new image tag.
   - Deploy to the Production DOKS cluster (`app-green` namespace).
   - Verify zero-downtime rolling update with `kubectl rollout status deployment/valstorm-mcp -n app-green`.

---

## 3. Manual Deployment Instructions

If you need to trigger a manual deployment from your local terminal:

### Step 3.1: Authenticate with DigitalOcean Container Registry & Clusters
```bash
# Login to DigitalOcean Container Registry
doctl registry login

# Save Kubernetes cluster kubeconfig
# For Dev:
doctl kubernetes cluster kubeconfig save valstorm-dev

# For Prod:
doctl kubernetes cluster kubeconfig save valstorm-prod
```

### Step 3.2: Build and Push Docker Image

#### For Dev:
```bash
TAG=$(git rev-parse --short HEAD)
docker build -t registry.digitalocean.com/canon-connect/valstorm-mcp-server-dev:${TAG} apps/valstorm-mcp-server
docker push registry.digitalocean.com/canon-connect/valstorm-mcp-server-dev:${TAG}
```

#### For Prod:
```bash
TAG=$(git rev-parse --short HEAD)
docker build -t registry.digitalocean.com/canon-connect/valstorm-mcp-server-prod:${TAG} apps/valstorm-mcp-server
docker push registry.digitalocean.com/canon-connect/valstorm-mcp-server-prod:${TAG}
```

### Step 3.3: Apply Kubernetes Manifests

#### For Dev:
```bash
# Switch to dev context
kubectl config use-context do-nyc3-valstorm-dev
kubectl config set-context --current --namespace=app

# Apply deployment & service
sed "s|<IMAGE>|registry.digitalocean.com/canon-connect/valstorm-mcp-server-dev:${TAG}|g" k8s/dev/valstorm-mcp-dev.yaml | kubectl apply -f -

# Apply updated Ingress rules
kubectl apply -f k8s/dev/ingress.yaml

# Verify rollout status
kubectl rollout status deployment/valstorm-mcp-dev -n app
```

#### For Prod:
```bash
# Switch to prod context and namespace
kubectl config use-context do-nyc3-valstorm-prod
kubectl config set-context --current --namespace=app-green

# Apply deployment & service
sed "s|<IMAGE>|registry.digitalocean.com/canon-connect/valstorm-mcp-server-prod:${TAG}|g" k8s/main/valstorm-mcp.yaml | kubectl apply -f -

# Apply updated Ingress rules
kubectl apply -f k8s/main/ingress.yaml

# Verify rollout status
kubectl rollout status deployment/valstorm-mcp -n app-green
```

---

## 4. DNS & SSL / TLS Configuration

Ensure DNS records point to your DigitalOcean Ingress Load Balancers:

| Hostname | Target | Environment |
| :--- | :--- | :--- |
| `mcp-dev.valstorm.com` | Dev Ingress LoadBalancer (CNAME/A) | Dev |
| `mcp.valstorm.com` | Prod Ingress LoadBalancer (CNAME/A) | Production |

SSL certificates are provisioned automatically via cert-manager using the `letsencrypt-prod` cluster issuer defined in the ingress manifests.

---

## 5. Post-Deployment Verification & Smoke Tests

### 1. Health Check
```bash
curl -i https://mcp-dev.valstorm.com/health
curl -i https://mcp.valstorm.com/health
```
**Expected Response:**
```json
{
  "status": "healthy",
  "service": "valstorm-mcp-server",
  "version": "1.0.0",
  "uptime_seconds": 12.4,
  "environment": "prod",
  "profile": "vdk"
}
```

### 2. Status & Tool Registry Inspection
```bash
curl -s https://mcp.valstorm.com/status | jq .
```
**Expected Response:**
```json
{
  "service": "valstorm-mcp-server",
  "version": "1.0.0",
  "environment": "prod",
  "base_url": "https://api.valstorm.com",
  "api_base_url": "https://api.valstorm.com/v1",
  "tools_count": 16,
  "tools": [
    "vfs_search",
    "vfs_ask",
    "vfs_read_text",
    "vfs_browse",
    "vfs_move",
    "vfs_prepare_upload",
    "vfs_get_download_url",
    "vfs_delete_file",
    "record_query_sql",
    "record_query_mongo",
    "record_hydrate_batch",
    "record_get",
    "record_create",
    "record_update",
    "record_delete",
    "schema_inspect"
  ]
}
```

### 3. Verify the Streamable HTTP handshake

This is the transport Claude and other current MCP clients use. It is stateless,
so a single POST is a complete exchange -- no session id is issued or required.

```bash
curl -s -X POST https://mcp.valstorm.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer <VALSTORM_TOKEN>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '.result.tools | length'
```
**Expected:** `16`

Run it several times in a row. Every call must succeed. Intermittent
`404 {"error":{"message":"Session not found"}}` means the transport has reverted to
stateful mode (`stateless_http`) while more than one replica is serving traffic.

Without a token the same request must return `401` carrying a discovery pointer:

```bash
curl -si -X POST https://mcp.valstorm.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | grep -i www-authenticate
```
**Expected:**
```text
www-authenticate: Bearer realm="https://mcp.valstorm.com/mcp", resource_metadata="https://mcp.valstorm.com/.well-known/oauth-protected-resource/mcp"
```

### 4. Verify OAuth discovery

```bash
curl -s https://mcp.valstorm.com/.well-known/oauth-protected-resource/mcp | jq .
curl -s https://mcp.valstorm.com/.well-known/oauth-authorization-server | jq .
```

`resource` must be `https://mcp.valstorm.com/mcp` (this server), and `issuer` must be
`https://mcp.valstorm.com` (the origin serving the document). If either names
`api.valstorm.com`, discovery will fail validation in strict clients.

### 5. Verify the legacy SSE stream (optional)

```bash
curl -N -m 5 "https://mcp.valstorm.com/sse?token=<VALSTORM_TOKEN>"
```
**Expected Response:**
```text
event: endpoint
data: /messages/?session_id=...
```

> SSE remains mounted for older clients. It is stateful and therefore still depends on
> a client sticking to one replica for the life of its stream.

---

## 6. Client Configuration

The server speaks **Streamable HTTP at `/mcp`**, the current MCP standard, and keeps the
legacy SSE transport at `/sse` + `/messages/`.

### 6.1 Claude (custom connector)

Add a custom connector pointing at:

```text
https://mcp.valstorm.com/mcp
```

Discovery then proceeds automatically:

1. `POST /mcp` with no token returns `401` with
   `WWW-Authenticate: Bearer ... resource_metadata="https://mcp.valstorm.com/.well-known/oauth-protected-resource/mcp"`.
2. That document names `https://mcp.valstorm.com` as the authorization server.
3. `GET /.well-known/oauth-authorization-server` returns endpoints on the core API.
4. `POST /oauth2/register` returns the pre-provisioned public client `valstorm_mcp`.
5. Authorization code + PKCE (S256) runs against `https://api.valstorm.com/v1/oauth2/authorize`
   and `https://api.valstorm.com/v1/oauth2/token`.

No client secret is involved. `valstorm_mcp` is a public client, and the API's token
endpoint skips secret verification when PKCE is present. The callback
`https://claude.ai/api/mcp/auth_callback` is already accepted by `is_valid_redirect_uri()`
in `apps/api/app/auth/oauth_helpers.py`, so no API change is required.

### 6.2 Manual client id (registration shim disabled)

Set `VALSTORM_MCP_ENABLE_DCR=false` to stop this server answering registration requests.
Clients must then be configured by hand. In Claude's connector dialog open
**Advanced settings** and enter:

| Field | Value |
| :--- | :--- |
| Client ID | `valstorm_mcp` |
| Client Secret | *(leave blank -- public client)* |

### 6.3 Cursor / Claude Desktop (`mcpServers` format)

```json
{
  "mcpServers": {
    "valstorm": {
      "url": "https://mcp.valstorm.com/mcp"
    }
  }
}
```

> The old `"url": "https://mcp.valstorm.com/sse?token=YOUR_PAT"` form still reaches the
> legacy transport, but the MCP specification forbids credentials in the query string and
> Claude offers no way to supply one. Prefer OAuth against `/mcp`.

### 6.4 Environment variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `VALSTORM_MCP_PUBLIC_URL` | `https://mcp.valstorm.com` (prod), `https://mcp-dev.valstorm.com` (dev) | This server's externally reachable origin. Everything in the discovery documents derives from it, so it **must** match the hostname clients actually dial. |
| `VALSTORM_MCP_PATH` | `/mcp` | Path the Streamable HTTP transport is served on. Feeds the canonical resource URI. |
| `VALSTORM_MCP_OAUTH_CLIENT_ID` | `valstorm_mcp` | OAuth client the registration shim hands out. |
| `VALSTORM_MCP_ENABLE_DCR` | `true` | Whether `/oauth2/register` answers. |
| `VALSTORM_MCP_REQUIRE_AUTH` | `true` | Whether a token is required at all. |
| `VALSTORM_ENV` | `prod` | Selects the upstream API and the default public origin. |

If `VALSTORM_MCP_PUBLIC_URL` is wrong, the symptom is confusing: the handshake succeeds
but the OAuth round trip fails validation, because the `resource` a client was told to
request a token for does not match the server it is talking to.

---

## 7. Rollback & Troubleshooting

### Check Pod Logs
```bash
# Dev
kubectl logs -l app=valstorm-mcp-dev -n app -f

# Prod
kubectl logs -l app=valstorm-mcp -n app-green -f
```

### Rollback Deployment
```bash
# Dev
kubectl rollout undo deployment/valstorm-mcp-dev -n app

# Prod
kubectl rollout undo deployment/valstorm-mcp -n app-green
```
