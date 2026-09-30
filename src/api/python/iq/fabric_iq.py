"""
Fabric IQ - the business-data / semantic brain.

Provides the orchestrator with one vocabulary for water utility operations,
resolved against the Fabric Lakehouse in OneLake (gold tables) and the
Eventhouse/KQL database (real-time edge signals).

Three access paths, tried in order:

1. **Fabric Data Agent (NL2Ontology)** - natural-language questions answered
   against the published semantic model. Best answer quality, zero SQL authoring.
2. **KQL / SQL endpoint** - deterministic queries against the Eventhouse or the
   Lakehouse SQL analytics endpoint for numeric, time-series work.
3. **Local Delta/Parquet demo dataset** - so the solution demos with no Fabric
   capacity attached.

The ontology itself lives in ``fabric/ontology/water_utility_ontology.yaml`` and
is loaded here so agents can reason about entities, measures and relationships
without guessing column names.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import FabricIQSettings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass
class QueryResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    source: str = "fabric-iq"
    query: str = ""
    row_count: int = 0
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "columns": self.columns,
            "source": self.source,
            "query": self.query,
            "rowCount": self.row_count,
            "truncated": self.truncated,
        }


class FabricIQ:
    """Semantic + analytical access to water utility edge data in OneLake."""

    def __init__(self, settings: FabricIQSettings, credential=None, demo_data_path: str | None = None):
        self.settings = settings
        self._credential = credential
        self._demo_path = _REPO_ROOT / (demo_data_path or "data/customdata")
        self._ontology: dict | None = None
        self._kusto = None

    # ----------------------------------------------------------------- ontology
    @property
    def ontology(self) -> dict:
        """The Fabric IQ business ontology: entities, measures, relationships."""
        if self._ontology is None:
            path = _REPO_ROOT / self.settings.ontology_path
            if path.exists():
                import yaml

                self._ontology = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            else:
                logger.warning("Fabric IQ ontology not found at %s", path)
                self._ontology = {}
        return self._ontology

    def describe_ontology(self) -> str:
        """Compact ontology summary injected into every agent's system prompt."""
        onto = self.ontology
        if not onto:
            return "No ontology loaded."
        lines = [f"Ontology: {onto.get('name', 'water-utility')} v{onto.get('version', '1')}"]
        for entity in onto.get("entities", []):
            attrs = ", ".join(a["name"] for a in entity.get("attributes", [])[:12])
            lines.append(f"- {entity['name']} (table {entity.get('table', '?')}): {attrs}")
        measures = onto.get("measures", [])
        if measures:
            lines.append("Measures: " + ", ".join(f"{m['name']}" for m in measures))
        return "\n".join(lines)

    def resolve_entity(self, term: str) -> dict | None:
        """
        Map a user's word to an ontology entity.

        Resolution runs widest-to-narrowest so the most specific interpretation
        wins: entity name, then entity synonym, then an attribute's allowed
        value (so 'pump' -> Asset via assetClass), then an attribute name or
        attribute synonym (so 'chlorine' -> WaterQuality via chlorine_mg_l).
        Returns the entity dict with a ``matchedVia`` key describing the hop,
        which agents use to explain how they interpreted the question.
        """
        term_l = term.lower().strip()
        if not term_l:
            return None
        entities = self.ontology.get("entities", [])

        def tagged(entity: dict, via: str, detail: str = "") -> dict:
            out = dict(entity)
            out["matchedVia"] = f"{via}:{detail}" if detail else via
            return out

        for entity in entities:
            if entity.get("name", "").lower() == term_l:
                return tagged(entity, "entityName")
        for entity in entities:
            if term_l in [s.lower() for s in entity.get("synonyms", [])]:
                return tagged(entity, "entitySynonym")
        for entity in entities:
            for attr in entity.get("attributes", []):
                if term_l in [str(v).lower() for v in attr.get("allowedValues", []) or []]:
                    return tagged(entity, "allowedValue", attr["name"])
        for entity in entities:
            for attr in entity.get("attributes", []):
                name_l = attr.get("name", "").lower()
                if name_l == term_l or name_l.split("_")[0] == term_l:
                    return tagged(entity, "attributeName", attr["name"])
                if term_l in [s.lower() for s in attr.get("synonyms", []) or []]:
                    return tagged(entity, "attributeSynonym", attr["name"])
        return None

    def resolve_terms(self, text: str) -> list[dict]:
        """Resolve every ontology term mentioned in a free-text question."""
        seen: dict[str, dict] = {}
        words = [w for w in re.split(r"[^\w]+", text.lower()) if len(w) > 2]
        phrases = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
        for phrase in phrases:
            hit = self.resolve_entity(phrase)
            if hit and hit["name"] not in seen:
                hit = dict(hit)
                hit["matchedTerm"] = phrase
                seen[hit["name"]] = hit
        return list(seen.values())

    # -------------------------------------------------------- natural language
    async def ask(self, question: str, *, user_assertion: str | None = None) -> QueryResult:
        """Ask the Fabric Data Agent a natural-language question over the ontology."""
        if not self.settings.data_agent_url:
            logger.debug("Fabric Data Agent URL not set; falling back to local dataset")
            return self._query_local(question)
        try:
            import httpx

            token = await self._token("https://api.fabric.microsoft.com/.default")
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    self.settings.data_agent_url,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"question": question, "workspace": self.settings.workspace_name},
                )
                resp.raise_for_status()
                payload = resp.json()
            rows = payload.get("rows") or payload.get("data") or []
            return QueryResult(
                rows=rows,
                columns=list(rows[0].keys()) if rows else [],
                source="fabric-data-agent",
                query=payload.get("generatedQuery", question),
                row_count=len(rows),
            )
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("Fabric Data Agent call failed (%s); using local dataset", exc)
            return self._query_local(question)

    # ------------------------------------------------------------------- KQL
    async def query_kql(self, kql: str, *, limit: int = 500) -> QueryResult:
        """Run a KQL query against the Eventhouse holding real-time edge telemetry."""
        if not self.settings.kql_endpoint:
            return self._query_local(kql)
        try:
            from azure.kusto.data import ClientRequestProperties, KustoClient, KustoConnectionStringBuilder

            if self._kusto is None:
                kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
                    self.settings.kql_endpoint, self._credential
                )
                self._kusto = KustoClient(kcsb)
            props = ClientRequestProperties()
            props.application = "edge-iq"
            bounded = kql if "| take" in kql or "| limit" in kql else f"{kql}\n| take {limit}"
            response = self._kusto.execute(self.settings.kql_database, bounded, props)
            table = response.primary_results[0]
            columns = [c.column_name for c in table.columns]
            rows = [dict(zip(columns, row)) for row in table]
            return QueryResult(
                rows=rows,
                columns=columns,
                source="fabric-eventhouse",
                query=bounded,
                row_count=len(rows),
                truncated=len(rows) >= limit,
            )
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("KQL query failed (%s); using local dataset", exc)
            return self._query_local(kql)

    # ------------------------------------------------------------------- SQL
    async def query_lakehouse(self, sql: str, *, limit: int = 500) -> QueryResult:
        """Query gold Delta tables via the Lakehouse SQL analytics endpoint."""
        if not self.settings.sql_endpoint:
            return self._query_local(sql)
        try:
            import struct

            import pyodbc

            token = await self._token("https://database.windows.net/.default")
            token_bytes = token.encode("utf-16-le")
            token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
            conn_str = (
                "Driver={ODBC Driver 18 for SQL Server};"
                f"Server={self.settings.sql_endpoint};"
                f"Database={self.settings.lakehouse_name};"
                "Encrypt=yes;TrustServerCertificate=no;"
            )
            with pyodbc.connect(conn_str, attrs_before={1256: token_struct}) as conn:
                cursor = conn.cursor()
                cursor.execute(sql)
                columns = [c[0] for c in cursor.description]
                rows = [dict(zip(columns, r)) for r in cursor.fetchmany(limit)]
            return QueryResult(
                rows=rows, columns=columns, source="fabric-lakehouse", query=sql, row_count=len(rows)
            )
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("Lakehouse SQL query failed (%s); using local dataset", exc)
            return self._query_local(sql)

    # ------------------------------------------------------------ local demo
    def _query_local(self, question: str) -> QueryResult:
        """
        Answer from the bundled demo dataset.

        Picks the parquet/CSV whose ontology entity best matches the question so
        demos return realistic shapes without any Fabric capacity.
        """
        import csv

        entity_files = {
            "device": "devices.csv",
            "telemetry": "telemetry_hourly.csv",
            "alarm": "alarms.csv",
            "workorder": "workorders.csv",
            "waterquality": "water_quality.csv",
            "site": "sites.csv",
            "energy": "energy_hourly.csv",
            "leak": "leak_events.csv",
        }
        q = question.lower()
        target = "telemetry_hourly.csv"
        for key, fname in entity_files.items():
            if key in q.replace(" ", "").replace("_", ""):
                target = fname
                break
        path = self._demo_path / target
        if not path.exists():
            return QueryResult(source="local-demo", query=question)
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = [row for _, row in zip(range(200), reader)]
        return QueryResult(
            rows=rows,
            columns=list(rows[0].keys()) if rows else [],
            source="local-demo",
            query=question,
            row_count=len(rows),
        )

    # ------------------------------------------------------------------ util
    async def _token(self, scope: str) -> str:
        token = await self._credential.get_token(scope)
        return token.token

    async def close(self) -> None:
        if self._kusto is not None:
            try:
                self._kusto.close()
            except Exception:
                pass
            self._kusto = None
