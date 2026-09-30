# =============================================================================
# Edge IQ - Orchestrator API
# =============================================================================
# Serves the FastAPI app in src/api/python: the Solution Orchestrator, the
# three IQ layers, the agent topology and the conversation store.
#
# Built from the repo root (context: .) so the app can read the committed
# knowledge base, ontology and demo dataset. Those are part of the product,
# not sample data: demo mode reads them directly, and even in a cloud
# deployment the ontology is loaded from disk.
# =============================================================================

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build tools for any wheel that needs compiling, removed in the same layer so
# they never reach the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first - this layer is cached across source changes.
COPY src/api/python/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && pip install --no-cache-dir gunicorn==23.0.0 uvicorn[standard]==0.34.0

COPY src/api/python/ /app/src/api/python/

# Grounding assets. The ontology and knowledge base are read at startup; the
# generated dataset backs demo mode and the local Fabric IQ fallback.
#
# The layout under /app deliberately mirrors the repo. foundry_iq.py and
# fabric_iq.py resolve the repo root by walking up from their own file
# (parents[4]), so flattening src/api/python to /app would make them resolve
# above the filesystem root and silently lose the corpus.
COPY fabric/ontology/ /app/fabric/ontology/
COPY documents/knowledge-base/ /app/documents/knowledge-base/
COPY data/customdata/ /app/data/customdata/

COPY gunicorn.conf.py /app/gunicorn.conf.py

# Non-root. Container Apps does not require it, but an OT-adjacent workload
# should not be the exception.
RUN useradd --create-home --uid 10001 edgeiq \
 && chown -R edgeiq:edgeiq /app
USER edgeiq

ENV PYTHONPATH=/app/src/api/python \
    EDGEIQ_DEMO_DATA_PATH=data/customdata \
    FABRIC_ONTOLOGY_PATH=fabric/ontology/water_utility_ontology.yaml \
    PORT=8000

EXPOSE 8000

# Container Apps probes this. It reports per-subsystem status, so a degraded
# grounding layer surfaces as degraded rather than silently returning worse
# answers.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:${PORT}/api/health || exit 1

CMD ["gunicorn", "app:app", "--config", "/app/gunicorn.conf.py"]
