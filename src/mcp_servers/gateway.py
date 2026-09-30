"""
Edge IQ MCP Gateway.

Mounts all five MCP servers behind one HTTP endpoint so the Container App only
exposes a single port and agents use one base URL:

    /mcp/edge-fleet       UC5  fleet, twins, certificates, OTA
    /mcp/historian        UC1/3/4  time-series, statistics, anomalies, data quality
    /mcp/asset-registry   UC1/4  nameplate, hierarchy, FMEA, ISO limits, spares
    /mcp/water-quality    UC2/3  quality, regulatory limits, DMA flow/pressure, NRW
    /mcp/work-order       all  priority matrix, crews, cost, gated writes

Run locally:   python -m src.mcp_servers.gateway
Container:     uvicorn src.mcp_servers.gateway:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import os

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .compat import http_app, list_tools as _list_tools
from .servers import asset_registry, edge_fleet, historian, water_quality, work_order

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("edgeiq.mcp")

SERVERS = {
    "edge-fleet": edge_fleet.mcp,
    "historian": historian.mcp,
    "asset-registry": asset_registry.mcp,
    "water-quality": water_quality.mcp,
    "work-order": work_order.mcp,
}


async def health(_request):
    from .datasource import DATA_PATH, backend

    return JSONResponse(
        {
            "status": "healthy",
            "backend": backend(),
            "dataPath": str(DATA_PATH),
            "writesEnabled": os.getenv("EDGEIQ_ALLOW_WRITES", "false").lower() == "true",
            "servers": {name: f"/mcp/{name}" for name in SERVERS},
        }
    )


async def catalog(_request):
    """Tool catalog for documentation, the UI and Copilot Studio connector generation."""
    out = {}
    for name, server in SERVERS.items():
        try:
            tools = await _list_tools(server)
            out[name] = [
                {"name": t.name, "description": (t.description or "").strip().split("\n")[0]}
                for t in tools
            ]
        except Exception as exc:  # pragma: no cover
            out[name] = [{"error": str(exc)}]
    return JSONResponse({"servers": out})


routes = [
    Route("/health", health),
    Route("/catalog", catalog),
    *[Mount(f"/mcp/{name}", app=http_app(server)) for name, server in SERVERS.items()],
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
