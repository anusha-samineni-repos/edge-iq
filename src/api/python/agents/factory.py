"""
Agent factory.

Builds runtime agents from ``AgentSpec`` definitions using the Microsoft Agent
Framework, attaching:

  * the Foundry chat client (Microsoft Foundry project)
  * the MCP tool servers each specialist is entitled to
  * the unified-context grounding block

and wires them into the orchestration topology selected by
``EDGEIQ_ORCHESTRATION_MODE`` (magentic | handoff | single).

If ``agent-framework`` is not installed (local dev, CI, demo mode), a fully
functional local fallback is used so the API, tests and demo all still run.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from .registry import AgentSpec, ORCHESTRATOR, SPECIALIST_SPECS

logger = logging.getLogger(__name__)


def _agent_framework_available() -> bool:
    try:
        import agent_framework  # noqa: F401

        return True
    except Exception:
        return False


async def build_agents(
    settings: Settings,
    credential: Any,
    *,
    mcp_tools: dict[str, Any] | None = None,
    specs: list[AgentSpec] | None = None,
) -> dict[str, Any]:
    """
    Instantiate one runtime agent per spec.

    ``mcp_tools`` maps MCP server name -> tool collection, produced by
    ``tools.mcp_client.load_mcp_tools``. Each agent only receives the servers
    listed in its spec, which is how least-privilege tool access is enforced.
    """
    specs = specs or SPECIALIST_SPECS
    mcp_tools = mcp_tools or {}

    if settings.orchestrator.demo_mode or not _agent_framework_available():
        logger.info("Building local fallback agents (demo_mode=%s)", settings.orchestrator.demo_mode)
        return {spec.name: _LocalAgent(spec, settings) for spec in specs}

    from agent_framework.azure import AzureAIAgentClient

    agents: dict[str, Any] = {}
    for spec in specs:
        tools: list[Any] = []
        for server_name in spec.mcp_servers:
            server_tools = mcp_tools.get(server_name)
            if server_tools:
                tools.extend(server_tools if isinstance(server_tools, list) else [server_tools])
            else:
                logger.warning("MCP server '%s' unavailable for agent '%s'", server_name, spec.name)

        client = AzureAIAgentClient(
            project_endpoint=settings.foundry_iq.project_endpoint,
            model_deployment_name=settings.foundry_iq.chat_deployment,
            async_credential=credential,
        )
        agents[spec.name] = client.create_agent(
            name=spec.name,
            instructions=spec.instructions,
            tools=tools or None,
            temperature=spec.temperature,
        )
        logger.info(
            "Built agent '%s' with %d MCP tool group(s): %s",
            spec.name,
            len(spec.mcp_servers),
            ", ".join(spec.mcp_servers) or "none",
        )
    return agents


async def build_orchestrator(
    settings: Settings,
    credential: Any,
    agents: dict[str, Any],
) -> Any:
    """
    Build the orchestration topology.

    magentic : Agent Framework ``MagenticBuilder`` - a manager agent plans, delegates
               to specialists over multiple rounds, and synthesizes. Highest quality
               for cross-domain questions. This is the Microsoft IQ default.
    handoff  : A router agent transfers the turn to exactly one specialist and that
               specialist owns the reply. Lowest latency, clearest attribution.
    single   : One agent holding every tool. Use only for smoke tests.
    """
    mode = settings.orchestrator.mode.lower()

    if settings.orchestrator.demo_mode or not _agent_framework_available():
        return _LocalOrchestrator(settings, agents)

    from agent_framework import MagenticBuilder
    from agent_framework.azure import AzureAIAgentClient

    manager_client = AzureAIAgentClient(
        project_endpoint=settings.foundry_iq.project_endpoint,
        model_deployment_name=settings.foundry_iq.chat_deployment,
        async_credential=credential,
    )

    if mode == "magentic":
        return (
            MagenticBuilder()
            .participants(**agents)
            .with_standard_manager(
                chat_client=manager_client,
                instructions=ORCHESTRATOR.instructions,
                max_round_count=settings.orchestrator.max_rounds,
                max_stall_count=settings.orchestrator.max_stall_rounds,
            )
            .build()
        )

    if mode == "handoff":
        from agent_framework import HandoffBuilder

        router = manager_client.create_agent(
            name=ORCHESTRATOR.name,
            instructions=ORCHESTRATOR.instructions,
            temperature=ORCHESTRATOR.temperature,
        )
        builder = HandoffBuilder(participants=[router, *agents.values()]).set_coordinator(router)
        for name in agents:
            builder = builder.add_handoff(router, agents[name])
        return builder.build()

    # single
    all_tools: list[Any] = []
    for agent in agents.values():
        all_tools.extend(getattr(agent, "tools", []) or [])
    return manager_client.create_agent(
        name=ORCHESTRATOR.name,
        instructions=ORCHESTRATOR.instructions,
        tools=all_tools or None,
        temperature=ORCHESTRATOR.temperature,
    )


# ---------------------------------------------------------------------------
# Local fallback implementations - keep demo/test paths fully functional
# ---------------------------------------------------------------------------
class _LocalAgent:
    """
    Deterministic stand-in for a Foundry agent.

    Produces a structured, clearly-labelled analysis from the unified context so
    the whole solution (API, UI, routing, citations, traces) can be demonstrated
    and tested without any Azure resources.
    """

    def __init__(self, spec: AgentSpec, settings: Settings):
        self.spec = spec
        self.settings = settings
        self.name = spec.name

    async def run(self, prompt: str, **_: Any) -> str:
        return await self._compose_async(prompt)

    async def run_stream(self, prompt: str, **_: Any):
        for line in (await self._compose_async(prompt)).splitlines(keepends=True):
            yield line

    async def _compose_async(self, prompt: str) -> str:
        from .demo_findings import compose

        try:
            findings = await compose(self.name, prompt)
        except Exception as exc:  # keep the demo resilient
            findings = f"_Tool call failed in demo mode: {exc}_"
        if not findings:
            return self._compose(prompt)
        return (
            f"{findings}\n\n"
            f"_Source: {self.spec.display_name} via MCP tools ({', '.join(self.spec.mcp_servers) or 'knowledge'}) "
            f"on demo data - no LLM was called._\n"
        )

    def _compose(self, prompt: str) -> str:
        context_block = ""
        if "## GROUNDED CONTEXT" in prompt:
            context_block = prompt.split("## GROUNDED CONTEXT", 1)[1]
        has_data = "### Fabric IQ data" in context_block
        has_knowledge = "### Foundry IQ knowledge" in context_block
        has_work = "### Work IQ context (Microsoft 365)" in context_block

        used = [
            layer
            for layer, present in (
                ("Fabric IQ", has_data),
                ("Foundry IQ", has_knowledge),
                ("Work IQ", has_work),
            )
            if present
        ]
        return (
            f"**{self.spec.display_name}** ({self.spec.use_case})\n\n"
            f"{self.spec.summary}\n\n"
            f"_Running in local demo mode - no Foundry model was called._\n\n"
            f"Grounding available this turn: {', '.join(used) or 'none'}.\n"
            f"Tools this specialist would use: {', '.join(self.spec.mcp_servers) or 'none'}.\n\n"
            "Deploy with `azd up` and unset EDGEIQ_DEMO_MODE to get model-generated analysis."
        )


class _LocalOrchestrator:
    """Local Magentic stand-in: pre-routes, runs the chosen specialists, fuses."""

    def __init__(self, settings: Settings, agents: dict[str, Any]):
        self.settings = settings
        self.agents = agents

    async def run(self, prompt: str, *, agent_names: list[str] | None = None, **_: Any) -> str:
        from .routing import route

        question = prompt.split("## GROUNDED CONTEXT")[0].strip()
        names = agent_names or route(question).agents or ["knowledge-navigator"]
        sections = []
        for name in names:
            agent = self.agents.get(name)
            if agent is None:
                continue
            sections.append(await agent.run(prompt))
        header = (
            "### Edge IQ Solution Orchestrator (demo mode)\n"
            f"Routed to: {', '.join(names)}\n"
        )
        return header + "\n\n---\n\n".join(sections)
