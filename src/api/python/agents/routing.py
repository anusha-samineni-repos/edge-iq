"""
Deterministic pre-router.

The Magentic manager is a strong planner but it costs a model round-trip. For
the large majority of operator questions the right specialist is obvious from
vocabulary alone. This module scores each specialist against the question and:

  * returns a confident single route  -> orchestrator delegates directly (fast path)
  * returns several plausible routes  -> orchestrator runs the full Magentic plan
  * returns nothing                   -> orchestrator runs the full Magentic plan

Routing decisions are recorded to Cosmos ``agent_traces`` so operators can audit
why a given specialist answered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .registry import SPECIALIST_SPECS, AgentSpec


@dataclass
class RouteDecision:
    agents: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    strategy: str = "plan"  # "direct" | "parallel" | "plan"
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "agents": self.agents,
            "scores": self.scores,
            "confidence": round(self.confidence, 3),
            "strategy": self.strategy,
            "rationale": self.rationale,
        }


# Phrases that must always involve a given specialist regardless of score.
_HARD_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(no|missing|lost|stopped|not)\s+(\w+\s+){0,2}"
                r"(data|telemetry|readings?|signal|reporting|responding|communicating)\b"),
     "fleet-operations",
     "A data-absence question is a device fault until proven otherwise."),
    (re.compile(r"\b(offline|unreachable|not reporting|stopped reporting|disconnected|"
                r"dropped off|went dark|flat ?lin\w*|no heartbeat)\b"),
     "fleet-operations",
     "Connectivity language maps to the edge fleet."),
    (re.compile(r"\b(raise|create|open|log|issue)\s+(a\s+|an\s+)?(work\s?order|ticket|job|wo)\b"),
     "maintenance-planner",
     "Explicit work-order creation request."),
    (re.compile(r"\b(boil water|public notice|tier 1|acute|do not drink)\b"), "water-quality",
     "Public-health escalation language."),
    (re.compile(r"\b(sop|procedure|how do i|step[- ]by[- ]step|runbook|work instruction)\b"),
     "knowledge-navigator",
     "Procedural request."),
    (re.compile(r"\b(certificat\w*|certs?)\b.*\b(expir\w*|renew\w*|rotat\w*)\b"), "fleet-operations",
     "Device identity lifecycle belongs to fleet operations."),
    (re.compile(r"\b(module twin|twin drift|deployment manifest|config drift)\b"), "fleet-operations",
     "IoT Edge configuration management."),
]


_BRIEFING = re.compile(
    r"\b(briefing|morning report|shift handover|estate|whole (fleet|network|system)|"
    r"overall status|status overview|what needs my attention|summary of everything)\b"
)
_BRIEFING_AGENTS = ["fleet-operations", "asset-health", "water-quality", "leak-detection", "energy-optimizer"]


def _tokenize(text: str) -> list[str]:
    tokens = [t for t in re.split(r"\W+", text.lower()) if t]
    # Tolerate simple plurals so "certificates" hits the "certificate" trigger.
    extra = [t[:-1] for t in tokens if len(t) > 3 and t.endswith("s") and not t.endswith("ss")]
    return tokens + extra


def route(question: str, *, entities: list[str] | None = None) -> RouteDecision:
    """Score every specialist against *question* and pick a strategy."""
    tokens = set(_tokenize(question))
    lowered = question.lower()
    entities = entities or []

    if _BRIEFING.search(lowered):
        return RouteDecision(
            agents=list(_BRIEFING_AGENTS),
            scores={name: 1.0 for name in _BRIEFING_AGENTS},
            confidence=1.0,
            strategy="parallel",
            rationale="Estate-wide briefing: running every operational specialist in parallel.",
        )

    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}

    for spec in SPECIALIST_SPECS:
        score = 0.0
        matched: list[str] = []
        for trigger in spec.triggers:
            if " " in trigger:
                if trigger in lowered:
                    score += 2.5
                    matched.append(trigger)
            elif trigger in tokens:
                score += 1.5
                matched.append(trigger)
        for entity in spec.entities:
            if entity in entities:
                score += 1.0
                matched.append(f"entity:{entity}")
        if score:
            scores[spec.name] = score
            hits[spec.name] = matched

    forced: list[str] = []
    rationale_parts: list[str] = []
    for pattern, agent, why in _HARD_RULES:
        if pattern.search(lowered):
            scores[agent] = scores.get(agent, 0.0) + 6.0
            hits.setdefault(agent, []).append("rule")
            if agent not in forced:
                forced.append(agent)
                rationale_parts.append(why)

    if not scores:
        return RouteDecision(
            strategy="plan",
            rationale="No specialist vocabulary matched; deferring to the Magentic planner.",
        )

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    total = sum(scores.values()) or 1.0
    confidence = top_score / total

    # Clear winner and enough absolute signal -> go straight there.
    if confidence >= 0.55 and top_score >= 3.0:
        agents = forced if forced else [top_name]
        if top_name not in agents:
            agents.append(top_name)
        rationale_parts.insert(
            0, f"'{top_name}' matched on: {', '.join(hits.get(top_name, [])[:6])}."
        )
        return RouteDecision(
            agents=agents,
            scores=scores,
            confidence=confidence,
            strategy="direct" if len(agents) == 1 else "parallel",
            rationale=" ".join(rationale_parts),
        )

    # Two strong, comparable candidates -> run both and let the orchestrator fuse.
    if runner_up >= 3.0 and (top_score - runner_up) <= 2.0:
        agents = [name for name, _ in ranked[:2]]
        for agent in forced:
            if agent not in agents:
                agents.append(agent)
        rationale_parts.insert(
            0, f"Question spans {' and '.join(agents)}; running them together."
        )
        return RouteDecision(
            agents=agents,
            scores=scores,
            confidence=confidence,
            strategy="parallel",
            rationale=" ".join(rationale_parts),
        )

    rationale_parts.insert(0, "Signal was ambiguous; using the full Magentic plan.")
    return RouteDecision(
        agents=[name for name, _ in ranked[:3]],
        scores=scores,
        confidence=confidence,
        strategy="plan",
        rationale=" ".join(rationale_parts),
    )


def describe_routing_table() -> list[dict]:
    """Used by GET /api/agents so the UI and docs stay in sync with the code."""
    return [
        {
            "name": spec.name,
            "displayName": spec.display_name,
            "useCase": spec.use_case,
            "summary": spec.summary,
            "mcpServers": spec.mcp_servers,
            "iqLayers": spec.iq_layers,
            "triggers": spec.triggers,
            "sampleQuestions": spec.sample_questions,
            "requiresApprovalFor": spec.requires_approval_for,
        }
        for spec in SPECIALIST_SPECS
    ]


def agent_by_name(name: str) -> AgentSpec | None:
    for spec in SPECIALIST_SPECS:
        if spec.name == name:
            return spec
    return None
