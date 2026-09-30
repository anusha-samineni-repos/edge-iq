"""
Gunicorn configuration for the Edge IQ orchestrator API.

Two settings here are load-bearing and should not be casually changed.

WORKER CLASS. The API streams Server-Sent Events and fans out to three IQ
layers concurrently with asyncio.gather. It must run on the uvicorn asyncio
worker; a synchronous worker would serialise every grounding call and turn a
2-second answer into a 6-second one.

TIMEOUT. A planned multi-agent turn legitimately takes over a minute: the
Magentic manager plans, several specialists run, each makes MCP tool calls.
The default 30-second timeout kills those mid-flight, and the failure looks
like a model error rather than a timeout, which is a miserable thing to
debug. This is aligned to EDGEIQ_REQUEST_TIMEOUT (default 180s) with headroom.
"""

import multiprocessing
import os

# --- binding ----------------------------------------------------------------
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# --- workers ----------------------------------------------------------------
worker_class = "uvicorn.workers.UvicornWorker"

# Each worker holds its own agent topology and MCP client pool, so workers are
# not free. Two per core is the right balance for an IO-bound orchestrator;
# cap it so a large Container Apps SKU does not spawn dozens of model clients.
_default_workers = min((multiprocessing.cpu_count() * 2) + 1, 8)
workers = int(os.getenv("GUNICORN_WORKERS", _default_workers))

# High per-worker concurrency is fine: almost all wall time is spent awaiting
# Foundry, Fabric, Graph and MCP.
worker_connections = int(os.getenv("GUNICORN_WORKER_CONNECTIONS", "1000"))

# --- timeouts ---------------------------------------------------------------
# Must exceed EDGEIQ_REQUEST_TIMEOUT or gunicorn will kill turns the
# orchestrator would have completed.
_request_timeout = int(os.getenv("EDGEIQ_REQUEST_TIMEOUT", "180"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", str(_request_timeout + 60)))

# Longer than any front-door idle timeout so SSE connections are not dropped
# mid-stream by the worker.
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "75"))
graceful_timeout = 30

# --- recycling --------------------------------------------------------------
# Guards against slow leaks in long-lived SDK clients. Jitter prevents every
# worker recycling at once and causing a latency cliff.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# --- lifecycle --------------------------------------------------------------
# Startup loads the ontology, the knowledge corpus and the agent topology.
# Booting workers one at a time would multiply that cost; preload does it once
# in the master. Safe here because nothing is bound before fork.
preload_app = os.getenv("GUNICORN_PRELOAD", "true").lower() == "true"

# --- logging ----------------------------------------------------------------
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

# Response time is included because the useful signal is not "did it work" but
# "how long did grounding take".
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'


def on_starting(server):
    server.log.info(
        "Edge IQ API starting - workers=%s timeout=%ss preload=%s demo_mode=%s",
        workers, timeout, preload_app,
        os.getenv("EDGEIQ_DEMO_MODE", "false"),
    )


def worker_int(worker):
    worker.log.info("Worker %s interrupted - draining in-flight turns", worker.pid)
