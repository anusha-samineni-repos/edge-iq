"""
Shared data access for the Edge IQ MCP servers.

Every MCP tool reads through this module so the servers behave identically in
three environments with no code change:

  demo        -> bundled CSV corpus in ``data/customdata`` (zero Azure)
  dev / prod  -> Cosmos DB for entities, Fabric Eventhouse (KQL) for telemetry

Switch with ``EDGEIQ_DATA_BACKEND=demo|azure`` (default: demo when no Cosmos
endpoint is set). This is the single knob that makes the servers "ready to
plug in" to a production environment.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = Path(os.getenv("EDGEIQ_DEMO_DATA_PATH", _ROOT / "data" / "customdata"))


def backend() -> str:
    explicit = os.getenv("EDGEIQ_DATA_BACKEND", "").strip().lower()
    if explicit in ("demo", "azure"):
        return explicit
    return "azure" if os.getenv("AZURE_COSMOS_ENDPOINT") else "demo"


@lru_cache(maxsize=32)
def load_csv(name: str) -> list[dict[str, Any]]:
    """Load ``data/customdata/{name}.csv`` with light type coercion."""
    path = DATA_PATH / f"{name}.csv"
    if not path.exists():
        logger.warning("Demo dataset '%s' not found at %s", name, path)
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [dict(row) for row in csv.DictReader(fh)]
    for row in rows:
        for key, value in list(row.items()):
            row[key] = _coerce(value)
    return rows


def _coerce(value: str) -> Any:
    if value is None or value == "":
        return None
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        if "." in value or "e" in low:
            return float(value)
        return int(value)
    except ValueError:
        return value


def filter_rows(
    rows: Iterable[dict],
    *,
    limit: int = 100,
    **criteria: Any,
) -> list[dict]:
    """Case-insensitive equality / membership filter used by every server."""
    out: list[dict] = []
    for row in rows:
        match = True
        for key, expected in criteria.items():
            if expected is None:
                continue
            actual = row.get(key)
            if isinstance(expected, (list, tuple, set)):
                if actual not in expected:
                    match = False
                    break
            elif isinstance(actual, str) and isinstance(expected, str):
                if actual.lower() != expected.lower():
                    match = False
                    break
            elif actual != expected:
                match = False
                break
        if match:
            out.append(row)
            if len(out) >= limit:
                break
    return out


def within_hours(rows: Iterable[dict], hours: int, field: str = "timestamp") -> list[dict]:
    if not hours:
        return list(rows)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for row in rows:
        raw = row.get(field)
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts >= cutoff:
            out.append(row)
    return out


def ok(data: Any, **meta: Any) -> dict:
    """Uniform MCP tool envelope so agents can reason about provenance."""
    payload = {"ok": True, "backend": backend(), "data": data}
    payload.update(meta)
    if isinstance(data, list):
        payload["count"] = len(data)
    return payload


def err(message: str, **meta: Any) -> dict:
    payload = {"ok": False, "backend": backend(), "error": message}
    payload.update(meta)
    return payload


# --------------------------------------------------------------------- Azure
async def cosmos_query(container: str, query: str, parameters: list[dict] | None = None) -> list[dict]:
    """Query a Cosmos container. Returns [] if Cosmos is unavailable."""
    endpoint = os.getenv("AZURE_COSMOS_ENDPOINT")
    if not endpoint:
        return []
    try:
        from azure.cosmos.aio import CosmosClient
        from azure.identity.aio import DefaultAzureCredential

        async with DefaultAzureCredential(exclude_interactive_browser_credential=True) as cred:
            async with CosmosClient(endpoint, credential=cred) as client:
                db = client.get_database_client(os.getenv("AZURE_COSMOS_DATABASE", "edgeiq"))
                cont = db.get_container_client(container)
                items = cont.query_items(query=query, parameters=parameters or [])
                return [item async for item in items]
    except Exception as exc:
        logger.warning("Cosmos query on '%s' failed: %s", container, exc)
        return []


async def kql_query(kql: str) -> list[dict]:
    """Query the Fabric Eventhouse (KQL). Returns [] if unavailable."""
    endpoint = os.getenv("FABRIC_KQL_ENDPOINT")
    database = os.getenv("FABRIC_KQL_DATABASE", "EdgeIQ_Telemetry")
    if not endpoint:
        return []
    try:
        import httpx
        from azure.identity.aio import DefaultAzureCredential

        async with DefaultAzureCredential(exclude_interactive_browser_credential=True) as cred:
            token = await cred.get_token("https://api.kusto.windows.net/.default")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{endpoint}/v1/rest/query",
                headers={"Authorization": f"Bearer {token.token}"},
                json={"db": database, "csl": kql},
            )
            resp.raise_for_status()
            payload = resp.json()
        table = next(
            (t for t in payload.get("Tables", []) if t.get("TableName") in (None, "Table_0", "PrimaryResult")),
            None,
        )
        if not table:
            return []
        columns = [c["ColumnName"] for c in table.get("Columns", [])]
        return [dict(zip(columns, row)) for row in table.get("Rows", [])]
    except Exception as exc:
        logger.warning("KQL query failed: %s", exc)
        return []
