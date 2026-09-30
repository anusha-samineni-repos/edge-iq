# =============================================================================
# Edge IQ - MCP tool gateway
# =============================================================================
# Serves the five MCP servers behind one ASGI app:
#
#   /mcp/edge-fleet      device fleet, connectivity, certificates, module twins
#   /mcp/historian       time-series telemetry and trend analysis
#   /mcp/asset-registry  asset master, ISO vibration bands, criticality
#   /mcp/water-quality   compliance assessment against regulatory limits
#   /mcp/work-order      maintenance work (writes are approval-gated)
#
# Deployed as its own container app rather than inside the API so tool access
# can be network-isolated and scaled independently. Agents reach it over
# Streamable HTTP; nothing else should be able to.
# =============================================================================

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY src/mcp_servers/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && pip install --no-cache-dir uvicorn[standard]==0.34.0

COPY src/mcp_servers/ /app/src/mcp_servers/

# The tool servers read the demo dataset when no Cosmos/Fabric backend is
# configured (datasource.backend() == "demo"). Shipping it means the gateway
# is useful the moment it starts, with or without Azure behind it.
#
# datasource.py resolves the repo root by walking up from its own file
# (parents[2]), so the src/ level must be preserved rather than flattened.
COPY data/customdata/ /app/data/customdata/
COPY documents/knowledge-base/ /app/documents/knowledge-base/

RUN useradd --create-home --uid 10001 edgeiq \
 && chown -R edgeiq:edgeiq /app
USER edgeiq

ENV PYTHONPATH=/app/src \
    EDGEIQ_DEMO_DATA_PATH=/app/data/customdata \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:${PORT}/health || exit 1

# One worker. The MCP servers hold per-session state for streaming transports,
# so horizontal scaling belongs at the container-app replica level, not in
# worker processes that cannot see each other's sessions.
CMD ["sh", "-c", "uvicorn mcp_servers.gateway:app --host 0.0.0.0 --port ${PORT} --workers 1"]
