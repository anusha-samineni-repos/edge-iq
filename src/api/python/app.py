"""
Edge IQ API host.

FastAPI app exposing the Solution Orchestrator, the IQ layer inspector, the
agent roster, conversation history and audit traces. Serves the React operator
console from ``static/`` when present so one container can host both.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .chat import router as chat_router
from .config import get_settings
from .history import router as history_router
from .orchestrator import get_orchestrator

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger("edgeiq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.app_insights_connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(connection_string=settings.app_insights_connection_string)
            logger.info("Azure Monitor OpenTelemetry configured.")
        except Exception as exc:
            logger.warning("Azure Monitor setup skipped: %s", exc)

    orchestrator = get_orchestrator()
    await orchestrator.startup()
    yield
    await orchestrator.shutdown()


app = FastAPI(
    title="Edge IQ",
    description=(
        "Multi-agent intelligence for water utility IoT Edge operations, built on "
        "the Microsoft IQ unified context pattern (Work IQ + Fabric IQ + Foundry IQ)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

_origins = [o for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(history_router)


@app.get("/api/health")
async def health():
    orchestrator = get_orchestrator()
    payload = orchestrator.health()
    code = 200 if payload["status"] == "ready" else 503
    return JSONResponse(payload, status_code=code)


@app.get("/api/version")
async def version():
    return {
        "name": "Edge IQ",
        "version": "1.0.0",
        "useCases": [
            "UC1 Asset health & predictive maintenance",
            "UC2 Water quality & regulatory compliance",
            "UC3 Leak detection & non-revenue water",
            "UC4 Pump energy & carbon optimisation",
            "UC5 Edge fleet operations & security",
        ],
        "iqLayers": ["Work IQ", "Fabric IQ", "Foundry IQ"],
    }


_static = Path(__file__).parent / "static"
if not _static.exists():
    # Local dev: serve the console straight from the Vite build output.
    _static = Path(__file__).resolve().parents[2] / "App" / "dist"
if _static.exists():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = _static / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_static / "index.html")
