FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace/apps/valstorm-mcp-server/src \
    PORT=8660

WORKDIR /workspace

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy monorepo dependencies referenced by [tool.uv.sources]
COPY packages/client-python /workspace/packages/client-python

# Copy dependency definition and install packages
COPY apps/valstorm-mcp-server/pyproject.toml /workspace/apps/valstorm-mcp-server/
WORKDIR /workspace/apps/valstorm-mcp-server
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application source code and install in editable mode
COPY apps/valstorm-mcp-server /workspace/apps/valstorm-mcp-server
RUN uv pip install --system --no-cache -e .

# Expose hosted MCP server port
EXPOSE 8660

# Health check endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8660/health || exit 1

# Launch the hosted MCP server. Both transports are mounted on the same ASGI app
# (Streamable HTTP on /mcp, legacy SSE on /sse + /messages); this flag only selects
# the non-stdio branch of the entrypoint. Claude and other current clients use /mcp.
CMD ["python", "-m", "valstorm_mcp.server", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8660"]
