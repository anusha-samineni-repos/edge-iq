"""
Edge IQ Solution Orchestrator.

This is the heart of the solution and the direct analogue of the orchestrator
agent in the Microsoft IQ / unified-data-foundation accelerator.

Per turn it:

  1. Loads conversation memory (Cosmos ``agent_memory``).
  2. Builds the unified context by fanning out across Work IQ, Fabric IQ and
     Foundry IQ concurrently (``iq.context.build_unified_context``).
  3. Pre-routes deterministically (``agents.routing.route``) to avoid a planner
     round-trip when the specialist is obvious.
  4. Delegates: direct/parallel to named specialists, or full Magentic planning
     when the question is ambiguous or spans domains.
  5. Gates any action an agent spec marks as requiring approval.
  6. Streams the answer, remapping Foundry citation markers to ``[n]``.
  7. Writes the turn to memory and a full decision trace to ``agent_traces``.

Lifecycle is owned by the FastAPI app: one ``Orchestrator`` per process.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .agents import ORCHESTRATOR, build_agents, build_orchestrator, get_agent_spec
from .agents.routing import RouteDecision, route
from .auth import get_credential_async
from .config import Settings, get_settings
from .iq import FabricIQ, FoundryIQ, WorkIQ, build_unified_context
from .memory import ConversationStore
from .tools import McpToolRegistry

logger = logging.getLogger(__name__)

# Foundry emits citations as 【3:0†source】; normalise to [3].
_MARKER_RE = re.compile(r"【(\d+)(?::\d+)?[^】]*】")


@dataclass
class TurnResult:
    conversation_id: str
    answer: str
    citations: list[dict] = field(default_factory=list)
    route: dict = field(default_factory=dict)
    layer_status: dict = field(default_factory=dict)
    agents_used: list[str] = field(default_factory=list)
    pending_approvals: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "conversationId": self.conversation_id,
            "answer": self.answer,
            "citations": self.citations,
            "route": self.route,
            "layerStatus": self.layer_status,
            "agentsUsed": self.agents_used,
            "pendingApprovals": self.pending_approvals,
            "elapsedMs": self.elapsed_ms,
        }


class Orchestrator:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.credential: Any = None
        self.foundry_iq: FoundryIQ | None = None
        self.fabric_iq: FabricIQ | None = None
        self.work_iq: WorkIQ | None = None
        self.store: ConversationStore | None = None
        self.mcp: McpToolRegistry | None = None
        self.agents: dict[str, Any] = {}
        self.topology: Any = None
        self._ready = False
        self._startup_error: str | None = None

    # ------------------------------------------------------------- lifecycle
    async def startup(self) -> None:
        started = time.perf_counter()
        try:
            self.credential = await get_credential_async()

            self.foundry_iq = FoundryIQ(self.settings.foundry_iq, credential=self.credential)
            self.fabric_iq = FabricIQ(
                self.settings.fabric_iq,
                credential=self.credential,
                demo_data_path=self.settings.orchestrator.demo_data_path,
            )
            self.work_iq = WorkIQ(self.settings.work_iq)

            self.store = await ConversationStore(self.settings, self.credential).connect()

            self.mcp = McpToolRegistry(self.settings)
            await self.mcp.load()

            self.agents = await build_agents(
                self.settings, self.credential, mcp_tools=self.mcp.tools
            )
            self.topology = await build_orchestrator(self.settings, self.credential, self.agents)

            self._ready = True
            logger.info(
                "Edge IQ orchestrator ready in %dms (mode=%s, agents=%d, demo=%s)",
                int((time.perf_counter() - started) * 1000),
                self.settings.orchestrator.mode,
                len(self.agents),
                self.settings.orchestrator.demo_mode,
            )
        except Exception as exc:  # pragma: no cover - startup resilience
            self._startup_error = str(exc)
            logger.exception("Orchestrator startup failed: %s", exc)

    async def shutdown(self) -> None:
        for closable in (self.foundry_iq, self.fabric_iq, self.work_iq, self.store, self.mcp):
            if closable is None:
                continue
            close = getattr(closable, "close", None)
            if close is None:
                continue
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # pragma: no cover
                logger.debug("Shutdown of %s raised: %s", type(closable).__name__, exc)

    # ---------------------------------------------------------------- health
    def health(self) -> dict:
        return {
            "status": "ready" if self._ready else "degraded",
            "startupError": self._startup_error,
            "orchestrationMode": self.settings.orchestrator.mode,
            "demoMode": self.settings.orchestrator.demo_mode,
            "agents": sorted(self.agents),
            "mcpServers": self.mcp.describe() if self.mcp else {},
            "memory": self.store.describe() if self.store else {},
            "iqLayers": {
                "foundryIQ": "enabled" if self.settings.foundry_iq.enabled else "disabled",
                "fabricIQ": "enabled" if self.settings.fabric_iq.enabled else "disabled",
                "workIQ": (
                    "enabled"
                    if self.settings.work_iq.enabled and self.work_iq and self.work_iq.configured
                    else "placeholder - register the M365 app to activate"
                ),
            },
            "settings": self.settings.describe(),
        }

    # ------------------------------------------------------------------ turn
    async def ask(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        user_id: str = "anonymous",
        user_assertion: str | None = None,
        max_classification: str | None = None,
    ) -> TurnResult:
        chunks: list[str] = []
        result: TurnResult | None = None
        async for event in self.ask_stream(
            question,
            conversation_id=conversation_id,
            user_id=user_id,
            user_assertion=user_assertion,
            max_classification=max_classification,
        ):
            if event["type"] == "delta":
                chunks.append(event["text"])
            elif event["type"] == "final":
                r = event["result"]
                result = TurnResult(
                    conversation_id=r["conversationId"],
                    answer=r["answer"],
                    citations=r["citations"],
                    route=r["route"],
                    layer_status=r["layerStatus"],
                    agents_used=r["agentsUsed"],
                    pending_approvals=r["pendingApprovals"],
                    elapsed_ms=r["elapsedMs"],
                )
            elif event["type"] == "error":
                raise RuntimeError(event["message"])
        if result is None:  # pragma: no cover
            result = TurnResult(conversation_id=conversation_id or "", answer="".join(chunks))
        if not result.answer:
            result.answer = "".join(chunks)
        return result

    async def ask_stream(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        user_id: str = "anonymous",
        user_assertion: str | None = None,
        max_classification: str | None = None,
    ) -> AsyncIterator[dict]:
        """
        Yields events:
          {"type": "status",   "stage": str, "detail": str}
          {"type": "route",    "route": dict}
          {"type": "delta",    "text": str}
          {"type": "final",    "result": dict}
          {"type": "error",    "message": str}
        """
        started = time.perf_counter()
        conversation_id = conversation_id or str(uuid.uuid4())
        classification = max_classification or self.settings.orchestrator.max_classification

        try:
            yield {"type": "status", "stage": "memory", "detail": "Loading conversation memory"}
            history = await self.store.get_history(conversation_id) if self.store else []

            yield {
                "type": "status",
                "stage": "context",
                "detail": "Assembling unified context across Work IQ, Fabric IQ and Foundry IQ",
            }
            context = await build_unified_context(
                question,
                foundry_iq=self.foundry_iq,
                fabric_iq=self.fabric_iq,
                work_iq=self.work_iq,
                user_assertion=user_assertion,
                max_classification=classification,
            )
            yield {
                "type": "status",
                "stage": "context",
                "detail": f"Context ready: {context.layer_status}",
            }

            decision = route(question, entities=context.entities)
            yield {"type": "route", "route": decision.to_dict()}

            approvals = self._pending_approvals(question, decision)
            prompt = self._build_prompt(question, history, context, decision, approvals)

            yield {
                "type": "status",
                "stage": "reasoning",
                "detail": f"Strategy '{decision.strategy}' via {', '.join(decision.agents) or 'Magentic planner'}",
            }

            answer_parts: list[str] = []
            out_of_scope = (
                not decision.agents
                and not context.knowledge
                and not context.device_ids
                and not context.site_ids
                and not context.entities
            )
            if out_of_scope:
                decision.rationale = "Out of scope: no water-utility vocabulary, assets or knowledge matched."
                text = (
                    "I'm Edge IQ, the assistant for the water utility's IoT Edge estate. That question "
                    "doesn't relate to our devices, sites, water quality, leakage, energy or maintenance, "
                    "so I can't answer it from grounded sources.\n\nTry, for example: *\"Which edge gateways "
                    "have certificates expiring in the next 30 days?\"* or *\"Is PUMP-003 at WTP-01 "
                    "healthy?\"*"
                )
                answer_parts.append(text)
                yield {"type": "delta", "text": text}
            else:
                async for text in self._run(prompt, decision):
                    clean = _MARKER_RE.sub(lambda m: f"[{m.group(1)}]", text)
                    answer_parts.append(clean)
                    yield {"type": "delta", "text": clean}

            answer = "".join(answer_parts).strip()
            elapsed = int((time.perf_counter() - started) * 1000)

            result = TurnResult(
                conversation_id=conversation_id,
                answer=answer,
                citations=context.citations(),
                route=decision.to_dict(),
                layer_status=context.layer_status,
                agents_used=decision.agents or (["out-of-scope"] if out_of_scope else ["magentic-plan"]),
                pending_approvals=approvals,
                elapsed_ms=elapsed,
            )

            if self.store:
                await self.store.append_turn(conversation_id, "user", question, user_id=user_id)
                await self.store.append_turn(
                    conversation_id,
                    "assistant",
                    answer,
                    user_id=user_id,
                    metadata={"agents": result.agents_used, "elapsedMs": elapsed},
                )
                if self.settings.orchestrator.enable_tracing:
                    await self.store.write_trace(
                        conversation_id,
                        {
                            "userId": user_id,
                            "question": question,
                            "route": decision.to_dict(),
                            "layerStatus": context.layer_status,
                            "deviceIds": context.device_ids,
                            "siteIds": context.site_ids,
                            "entities": context.entities,
                            "citations": result.citations,
                            "pendingApprovals": approvals,
                            "orchestrationMode": self.settings.orchestrator.mode,
                            "elapsedMs": elapsed,
                        },
                    )

            yield {"type": "final", "result": result.to_dict()}

        except Exception as exc:
            logger.exception("Turn failed: %s", exc)
            yield {"type": "error", "message": str(exc)}

    # ------------------------------------------------------------- internals
    async def _run(self, prompt: str, decision: RouteDecision) -> AsyncIterator[str]:
        if self.topology is None:
            yield "Edge IQ is still starting up. Check /api/health for details."
            return

        if decision.strategy in ("direct", "parallel") and decision.agents:
            for name in decision.agents:
                agent = self.agents.get(name)
                if agent is None:
                    continue
                if len(decision.agents) > 1:
                    yield f"\n\n### {get_agent_spec(name).display_name}\n"
                async for chunk in self._stream_agent(agent, prompt):
                    yield chunk
            return

        async for chunk in self._stream_agent(self.topology, prompt, decision=decision):
            yield chunk

    async def _stream_agent(
        self, agent: Any, prompt: str, *, decision: RouteDecision | None = None
    ) -> AsyncIterator[str]:
        kwargs: dict = {}
        if decision is not None and decision.agents:
            kwargs["agent_names"] = decision.agents

        run_stream = getattr(agent, "run_stream", None)
        if run_stream is not None:
            try:
                async for update in run_stream(prompt, **kwargs):
                    text = getattr(update, "text", None)
                    if text is None and isinstance(update, str):
                        text = update
                    if text:
                        yield text
                return
            except TypeError:
                async for update in run_stream(prompt):
                    text = getattr(update, "text", None) or (update if isinstance(update, str) else "")
                    if text:
                        yield text
                return

        response = await agent.run(prompt, **kwargs)
        yield getattr(response, "text", None) or str(response)

    def _build_prompt(
        self,
        question: str,
        history: list[dict],
        context: Any,
        decision: RouteDecision,
        approvals: list[dict],
    ) -> str:
        parts: list[str] = []

        if history:
            recent = history[-6:]
            transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in recent)
            parts.append(f"## CONVERSATION SO FAR\n{transcript}\n")

        parts.append(f"## OPERATOR QUESTION\n{question}\n")
        parts.append(context.to_prompt_block())

        if decision.agents:
            parts.append(
                "## ROUTING\n"
                f"Pre-router selected: {', '.join(decision.agents)} "
                f"(confidence {decision.confidence:.2f}, strategy {decision.strategy}).\n"
                f"Rationale: {decision.rationale}\n"
            )

        if approvals:
            names = ", ".join(a["action"] for a in approvals)
            parts.append(
                "## APPROVAL REQUIRED\n"
                f"This request may trigger gated actions ({names}). Do NOT execute them. "
                "Instead, produce the exact change you would make and end with a clearly "
                "labelled 'Proposed action - approval required' block for the operator to confirm.\n"
            )

        parts.append(
            "## RESPONSE RULES\n"
            "- Ground every factual claim in the context above and cite it as [n].\n"
            "- If a layer is missing or errored, say so plainly rather than guessing.\n"
            "- Lead with the answer, then evidence, then recommended next action.\n"
            "- Include device IDs, units and timestamps wherever you quote a value.\n"
        )
        return "\n".join(parts)

    _WRITE_SYNONYMS = {
        "create": ("create", "raise", "open", "log", "issue", "book", "schedule"),
        "update": ("update", "change", "modify", "amend", "reprioriti"),
        "assign": ("assign", "allocate", "dispatch"),
    }

    def _pending_approvals(self, question: str, decision: RouteDecision) -> list[dict]:
        approvals: list[dict] = []
        lowered = question.lower()
        for name in decision.agents:
            try:
                spec = get_agent_spec(name)
            except KeyError:
                continue
            for action in spec.requires_approval_for:
                verb = action.replace("_", " ")
                head = verb.split()[0]
                obj = verb.split(" ", 1)[1] if " " in verb else ""
                synonyms = self._WRITE_SYNONYMS.get(head, (head,))
                obj_hit = not obj or obj in lowered or obj.replace(" ", "") in lowered or (
                    "work order" in obj and re.search(r"\b(wo|ticket|job)\b", lowered)
                )
                if verb in lowered or action in lowered or (
                    obj_hit and any(re.search(rf"\b{s}", lowered) for s in synonyms)
                ):
                    approvals.append({"agent": name, "action": action})
        return approvals


_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
