"""
MCP SDK compatibility shim.

The Python MCP SDK renamed ``FastMCP`` to ``MCPServer`` in v2.x and changed the
HTTP app accessor. Edge IQ supports both so the servers keep working whether a
deployment pins ``mcp<2`` (as the Foundry samples currently do) or tracks 2.x.

Use ``build_server(name)`` instead of instantiating the SDK class directly, and
``http_app(server)`` instead of calling ``streamable_http_app()``.
"""

from __future__ import annotations

from typing import Any

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server  # type: ignore

    MCP_MAJOR = 2
except ImportError:  # mcp < 2.0
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore

    MCP_MAJOR = 1


def build_server(name: str) -> Any:
    """Create an MCP server instance for either SDK generation."""
    return _Server(name)


def http_app(server: Any) -> Any:
    """Return the Streamable HTTP ASGI app for this server."""
    for attr in ("streamable_http_app", "http_app", "asgi_app", "sse_app"):
        factory = getattr(server, attr, None)
        if callable(factory):
            return factory()
    raise RuntimeError(
        f"MCP server {server!r} exposes no known HTTP app factory "
        "(tried streamable_http_app/http_app/asgi_app/sse_app)."
    )


def tool_fn(tool: Any) -> Any:
    """
    Get the raw async callable behind a registered MCP tool.

    v1 exposes it as ``.fn``; v2 exposes ``.func``. Used by tests and the
    in-process demo path so tools can be called without an HTTP round-trip.
    """
    for attr in ("fn", "func", "callback", "handler"):
        candidate = getattr(tool, attr, None)
        if callable(candidate):
            return candidate
    if callable(tool):
        return tool
    raise RuntimeError(f"Cannot resolve callable for MCP tool {tool!r}")


async def list_tools(server: Any) -> list[Any]:
    """List tools across SDK generations."""
    lister = getattr(server, "list_tools", None)
    if lister is None:
        return []
    result = lister()
    if hasattr(result, "__await__"):
        result = await result
    return list(result)
