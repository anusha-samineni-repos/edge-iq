"""
Unified Context Layer.

Before any specialist agent runs, the orchestrator assembles a single
``UnifiedContext`` by fanning out across all three IQ brains in parallel:

    Work IQ     -> what people said/wrote about this asset
    Fabric IQ   -> what the data says about this asset (ontology-resolved)
    Foundry IQ  -> what the governed knowledge base says should happen

This is the concrete implementation of the Microsoft IQ pattern: agents never
query raw sources directly for grounding; they reason over one context object
whose provenance, freshness and classification are all explicit.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .fabric_iq import FabricIQ, QueryResult
from .foundry_iq import FoundryIQ, KnowledgeChunk
from .work_iq import WorkIQ, WorkIQResult

logger = logging.getLogger(__name__)

# Device IDs look like WTP-01-PUMP-003 / DMA-14-PRV-002 / RES-03-CL2-001
_DEVICE_RE = re.compile(r"\b([A-Z]{2,4}-\d{1,3}-[A-Z0-9]{2,6}-\d{1,3})\b")
_SITE_RE = re.compile(r"\b((?:WTP|WWTP|PS|RES|DMA|WELL)-\d{1,3})\b")


@dataclass
class UnifiedContext:
    """Everything the agents are allowed to reason over for one turn."""

    question: str
    device_ids: list[str] = field(default_factory=list)
    site_ids: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)

    knowledge: list[KnowledgeChunk] = field(default_factory=list)
    data: QueryResult | None = None
    work: WorkIQResult | None = None
    ontology_summary: str = ""

    assembled_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    layer_status: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ render
    def to_prompt_block(self, *, max_knowledge_chars: int = 6000) -> str:
        """Render the context as the grounding block injected into agent prompts."""
        parts: list[str] = ["## GROUNDED CONTEXT (Edge IQ unified context layer)"]

        if self.device_ids or self.site_ids:
            parts.append(
                "### Resolved assets\n"
                f"Devices: {', '.join(self.device_ids) or 'none detected'}\n"
                f"Sites: {', '.join(self.site_ids) or 'none detected'}"
            )

        if self.ontology_summary:
            parts.append(f"### Fabric IQ ontology\n{self.ontology_summary}")

        if self.data and self.data.row_count:
            preview = self.data.rows[:20]
            parts.append(
                "### Fabric IQ data ("
                f"{self.data.source}, {self.data.row_count} rows"
                f"{', truncated' if self.data.truncated else ''})\n"
                f"Query: {self.data.query}\n"
                f"{_rows_as_table(preview, self.data.columns)}"
            )

        if self.knowledge:
            budget = max_knowledge_chars
            lines = ["### Foundry IQ knowledge (cite as [n])"]
            for idx, chunk in enumerate(self.knowledge, start=1):
                body = chunk.content[: max(400, budget // max(1, len(self.knowledge)))]
                budget -= len(body)
                lines.append(
                    f"[{idx}] {chunk.title} "
                    f"(type={chunk.doc_type}, effective={chunk.effective_date or 'n/a'}, "
                    f"source={chunk.source})\n{body}"
                )
                if budget <= 0:
                    break
            parts.append("\n\n".join(lines))

        if self.work and self.work.items:
            lines = ["### Work IQ context (Microsoft 365)"]
            for item in self.work.items[:8]:
                lines.append(
                    f"- [{item.kind}] {item.title} - {item.author or 'unknown'} "
                    f"{item.timestamp}\n  {item.snippet[:240]}"
                )
            parts.append("\n".join(lines))
        elif self.work and self.work.note:
            parts.append(f"### Work IQ context\n{self.work.note}")

        parts.append(
            "### Grounding rules\n"
            "- Cite Foundry IQ knowledge with [n] markers matching the numbers above.\n"
            "- Never invent device IDs, readings, thresholds or regulatory limits.\n"
            "- If the context does not answer the question, say so and name the missing source."
        )
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "deviceIds": self.device_ids,
            "siteIds": self.site_ids,
            "entities": self.entities,
            "knowledge": [k.to_dict() for k in self.knowledge],
            "data": self.data.to_dict() if self.data else None,
            "work": self.work.to_dict() if self.work else None,
            "assembledAt": self.assembled_at,
            "layerStatus": self.layer_status,
        }

    def citations(self) -> list[dict]:
        return [
            {
                "index": idx,
                "title": chunk.title,
                "source": chunk.source,
                "docType": chunk.doc_type,
                "effectiveDate": chunk.effective_date,
            }
            for idx, chunk in enumerate(self.knowledge, start=1)
        ]


def _describe_status(layer: str, outcome: object) -> str:
    """Report honestly whether a layer hit a real backend or a local stand-in."""
    if layer == "work_iq":
        return "ok" if getattr(outcome, "configured", True) else "placeholder (M365 not configured)"
    if layer == "fabric_iq":
        source = getattr(outcome, "source", "")
        return "demo (local data)" if source == "local-demo" else "ok"
    if layer == "foundry_iq":
        return "ok"
    return "ok"


async def build_unified_context(
    question: str,
    *,
    foundry_iq: FoundryIQ | None = None,
    fabric_iq: FabricIQ | None = None,
    work_iq: WorkIQ | None = None,
    user_assertion: str | None = None,
    include_work_iq: bool = True,
    include_data: bool = True,
    max_classification: str = "internal",
) -> UnifiedContext:
    """Fan out across the three IQ layers concurrently and fuse the results."""
    device_ids = sorted(set(_DEVICE_RE.findall(question)))
    site_ids = sorted(set(_SITE_RE.findall(question)))
    # A device ID implies its site prefix (WTP-01-PUMP-003 -> WTP-01)
    for device in device_ids:
        parts = device.split("-")
        if len(parts) >= 2:
            site_ids.append(f"{parts[0]}-{parts[1]}")
    site_ids = sorted(set(site_ids))

    entities: list[str] = []
    ontology_summary = ""
    if fabric_iq is not None:
        ontology_summary = fabric_iq.describe_ontology()
        for token in re.split(r"\W+", question):
            if len(token) > 2 and fabric_iq.resolve_entity(token):
                entities.append(token.lower())
        entities = sorted(set(entities))

    tasks: dict[str, asyncio.Task] = {}
    if foundry_iq is not None and foundry_iq.settings.enabled:
        tasks["foundry_iq"] = asyncio.create_task(
            foundry_iq.retrieve(question, max_classification=max_classification)
        )
    if include_data and fabric_iq is not None and fabric_iq.settings.enabled:
        tasks["fabric_iq"] = asyncio.create_task(fabric_iq.ask(question, user_assertion=user_assertion))
    if include_work_iq and work_iq is not None and work_iq.settings.enabled:
        scope = " ".join(device_ids + site_ids) or question
        tasks["work_iq"] = asyncio.create_task(
            work_iq.search(scope, top=8, user_assertion=user_assertion)
        )

    results: dict[str, object] = {}
    layer_status: dict[str, str] = {}
    if tasks:
        done = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for (layer, _), outcome in zip(tasks.items(), done):
            if isinstance(outcome, Exception):
                logger.warning("IQ layer %s failed: %s", layer, outcome)
                layer_status[layer] = f"error: {outcome}"
            else:
                results[layer] = outcome
                layer_status[layer] = _describe_status(layer, outcome)

    for layer in ("foundry_iq", "fabric_iq", "work_iq"):
        layer_status.setdefault(layer, "skipped")
    if layer_status.get("foundry_iq") == "ok" and getattr(foundry_iq, "last_backend", "") == "local":
        layer_status["foundry_iq"] = "local (knowledge base files)"

    return UnifiedContext(
        question=question,
        device_ids=device_ids,
        site_ids=site_ids,
        entities=entities,
        knowledge=results.get("foundry_iq") or [],  # type: ignore[arg-type]
        data=results.get("fabric_iq"),  # type: ignore[arg-type]
        work=results.get("work_iq"),  # type: ignore[arg-type]
        ontology_summary=ontology_summary,
        layer_status=layer_status,
    )


def _rows_as_table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "(no rows)"
    cols = columns or list(rows[0].keys())
    header = " | ".join(cols)
    divider = "-|-".join("-" * len(c) for c in cols)
    body = "\n".join(" | ".join(str(r.get(c, "")) for c in cols) for r in rows)
    return f"{header}\n{divider}\n{body}"
