"""
MCP client loader.

Edge IQ exposes five MCP servers behind one gateway (``src/mcp/gateway.py``).
Each server is reachable over Streamable HTTP at ``{gateway}/mcp/{server}``.

This module connects to each server the agents need and returns tool collections
keyed by server name, so ``agents.factory.build_agents`` can hand each specialist
only the servers its spec entitles it to. That is how least-privilege tool access
is enforced - ``water-quality`` can never create a work order.

Every server is optional: if one is unreachable the agent is still built, just
without those tools, and the degradation is reported on ``/api/health``.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from ..config import Settings

logger = logging.getLogger(__name__)

MCP_SERVERS = [
    "edge-fleet",
    "historian",
    "asset-registry",
    "water-quality",
    "work-order",
]


class McpToolRegistry:
    """Owns MCP connections for the lifetime of the app."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._stack = AsyncExitStack()
        self.tools: dict[str, Any] = {}
        self.status: dict[str, str] = {}

    async def __aenter__(self) -> "McpToolRegistry":
        await self.load()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def load(self) -> dict[str, Any]:
        if self.settings.orchestrator.demo_mode:
            for name in MCP_SERVERS:
                self.status[name] = "demo - tools simulated from local CSV corpus"
            return {}

        try:
            from agent_framework import MCPStreamableHTTPTool
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.warning("agent-framework MCP support unavailable: %s", exc)
            for name in MCP_SERVERS:
                self.status[name] = f"unavailable: {exc}"
            return {}

        base = self.settings.orchestrator.mcp_gateway_url.rstrip("/")
        for name in MCP_SERVERS:
            url = f"{base}/mcp/{name}"
            try:
                tool = MCPStreamableHTTPTool(name=f"edgeiq-{name}", url=url)
                await self._stack.enter_async_context(tool)
                self.tools[name] = tool
                self.status[name] = "connected"
                logger.info("MCP server '%s' connected at %s", name, url)
            except Exception as exc:
                self.status[name] = f"error: {exc}"
                logger.warning("MCP server '%s' failed at %s: %s", name, url, exc)
        return self.tools

    async def close(self) -> None:
        try:
            await self._stack.aclose()
        except Exception as exc:  # pragma: no cover
            logger.debug("MCP shutdown raised: %s", exc)
        self.tools.clear()

    def describe(self) -> dict[str, str]:
        return dict(self.status)


async def load_mcp_tools(settings: Settings) -> McpToolRegistry:
    registry = McpToolRegistry(settings)
    await registry.load()
    return registry
