#!/usr/bin/env python3
"""
Register the Edge IQ agents in Microsoft Foundry.

The registry in ``src/api/python/agents/registry.py`` is the single source of
truth for every agent's name, instructions, tools and grounding layers. This
script projects that registry into Foundry so the cloud-hosted agents and the
locally-running ones can never drift apart. Re-running it updates existing
agents in place rather than creating duplicates.

It also emits ``copilot-studio/generated/agent-tools.json`` - the tool manifest
the Copilot Studio front-door agent uses to describe each specialist to the
user. That file is generated rather than hand-written for the same reason.

Usage
-----
    python register_agents.py --dry-run         # show what would be created
    python register_agents.py                   # create/update in Foundry
    python register_agents.py --emit-manifest   # regenerate the CS manifest
    python register_agents.py --delete          # remove all Edge IQ agents
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]

# Load the registry module directly rather than importing the `agents` package.
# `agents/__init__.py` pulls in factory.py, which uses package-relative imports
# (`from ..config import Settings`) that only resolve when the API is loaded as
# the `api.python` package. The registry itself has no such dependency, so we
# load the single module by path and keep this script free of the API's
# runtime requirements.
import importlib.util  # noqa: E402

_REG_PATH = REPO_ROOT / "src" / "api" / "python" / "agents" / "registry.py"
_spec = importlib.util.spec_from_file_location("edgeiq_registry", _REG_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise SystemExit(f"Cannot load agent registry from {_REG_PATH}")
_registry = importlib.util.module_from_spec(_spec)
# dataclass() resolves the defining module out of sys.modules, so the module
# must be registered there *before* it executes.
sys.modules["edgeiq_registry"] = _registry
_spec.loader.exec_module(_registry)

AGENT_SPECS = _registry.AGENT_SPECS
ORCHESTRATOR = _registry.ORCHESTRATOR
SPECIALIST_SPECS = _registry.SPECIALIST_SPECS

MANIFEST_PATH = REPO_ROOT / "copilot-studio" / "generated" / "agent-tools.json"

# Marker written into agent metadata so --delete only ever touches our agents.
TAG_KEY = "solution"
TAG_VALUE = "edge-iq"


def _mcp_tool_definitions(spec: Any, gateway_url: str) -> list[dict[str, Any]]:
    """Each MCP server the agent is entitled to becomes one Foundry MCP tool.

    Entitlement is per-agent, not global: the water quality specialist cannot
    reach the work-order server, so it cannot propose a write even if a prompt
    tries to talk it into one. That boundary is enforced here at registration
    time, not in the model's instructions.
    """
    return [
        {
            "type": "mcp",
            "server_label": server,
            "server_url": f"{gateway_url.rstrip('/')}/mcp/{server}",
            # 'never' means the model may call the tool without a per-call
            # approval prompt. Write gating is enforced inside the tool itself
            # (approved=True plus EDGEIQ_ALLOW_WRITES), which is a stronger
            # guarantee than a model-side confirmation.
            "require_approval": "never",
        }
        for server in spec.mcp_servers
    ]


def _connected_agent_tools(gateway_url: str) -> list[dict[str, Any]]:
    """The orchestrator reaches specialists as connected agents, not MCP tools."""
    return [
        {
            "type": "connected_agent",
            "name": s.name,
            "description": f"{s.display_name} ({s.use_case}). {s.summary}",
        }
        for s in SPECIALIST_SPECS
    ]


def build_plan(gateway_url: str, model: str) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for spec in AGENT_SPECS:
        is_orchestrator = spec.name == ORCHESTRATOR.name
        tools = (_connected_agent_tools(gateway_url) if is_orchestrator
                 else _mcp_tool_definitions(spec, gateway_url))
        plan.append({
            "name": f"edgeiq-{spec.name}" if not spec.name.startswith("edgeiq-") else spec.name,
            "model": model,
            "description": spec.summary,
            "instructions": spec.instructions,
            "temperature": spec.temperature,
            "tools": tools,
            "metadata": {
                TAG_KEY: TAG_VALUE,
                "useCase": spec.use_case,
                "role": "orchestrator" if is_orchestrator else "specialist",
                "iqLayers": ",".join(spec.iq_layers),
                "requiresApprovalFor": ",".join(spec.requires_approval_for),
            },
        })
    return plan


def build_manifest(gateway_url: str) -> dict[str, Any]:
    """Tool manifest for the Copilot Studio front door."""
    return {
        "$comment": (
            "GENERATED by infra/scripts/seed/register_agents.py - do not edit. "
            "Regenerate after changing src/api/python/agents/registry.py."
        ),
        "solution": "edge-iq",
        "gatewayUrl": gateway_url,
        "orchestrator": {
            "name": ORCHESTRATOR.name,
            "displayName": ORCHESTRATOR.display_name,
            "summary": ORCHESTRATOR.summary,
        },
        "specialists": [
            {
                "name": s.name,
                "displayName": s.display_name,
                "useCase": s.use_case,
                "summary": s.summary,
                "mcpServers": s.mcp_servers,
                "iqLayers": s.iq_layers,
                "sampleQuestions": s.sample_questions,
                "requiresApprovalFor": s.requires_approval_for,
                "triggers": s.triggers,
                "entities": s.entities,
            }
            for s in SPECIALIST_SPECS
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Register Edge IQ agents in Foundry.")
    ap.add_argument("--project-endpoint",
                    default=os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", ""))
    ap.add_argument("--model",
                    default=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o"))
    ap.add_argument("--gateway-url",
                    default=os.getenv("EDGEIQ_MCP_GATEWAY_URL", "http://localhost:8080"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--emit-manifest", action="store_true",
                    help="Write the Copilot Studio tool manifest and exit.")
    ap.add_argument("--delete", action="store_true",
                    help="Delete every agent tagged solution=edge-iq.")
    args = ap.parse_args()

    plan = build_plan(args.gateway_url, args.model)

    print("Edge IQ - Foundry agent registration")
    print(f"  model    {args.model}")
    print(f"  gateway  {args.gateway_url}")
    print(f"  agents   {len(plan)} ({len(SPECIALIST_SPECS)} specialists + 1 orchestrator)")
    for item in plan:
        kinds = {t["type"] for t in item["tools"]}
        labels = [t.get("server_label") or t.get("name") for t in item["tools"]]
        print(f"    {item['name']:<34} {','.join(sorted(kinds)) or 'no tools':<16} "
              f"{len(item['tools'])} -> {', '.join(labels) if labels else '-'}")

    # The manifest is cheap and has no Azure dependency, so always refresh it.
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(build_manifest(args.gateway_url), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  manifest {MANIFEST_PATH.relative_to(REPO_ROOT)}")

    if args.emit_manifest:
        return 0

    if args.dry_run:
        print("\nDry run - nothing sent to Foundry.")
        return 0

    if not args.project_endpoint:
        print(
            "\nNo Foundry project endpoint.\n"
            "  Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT or pass --project-endpoint.\n"
            "  Edge IQ runs its full agent topology locally in demo mode "
            "(EDGEIQ_DEMO_MODE=true),\n"
            "  so Foundry registration is only needed for a cloud deployment."
        )
        return 0

    try:
        from azure.ai.agents import AgentsClient  # type: ignore
        from azure.identity import DefaultAzureCredential  # type: ignore
    except ImportError:
        raise SystemExit(
            "azure-ai-agents and azure-identity are required.\n"
            "  pip install azure-ai-agents azure-identity"
        )

    agents_api = AgentsClient(endpoint=args.project_endpoint,
                              credential=DefaultAzureCredential(
                                  exclude_interactive_browser_credential=True))

    existing = {}
    for agent in agents_api.list_agents():
        meta = getattr(agent, "metadata", None) or {}
        if meta.get(TAG_KEY) == TAG_VALUE:
            existing[agent.name] = agent.id

    if args.delete:
        for name, agent_id in existing.items():
            agents_api.delete_agent(agent_id)
            print(f"  deleted {name}")
        print(f"\nDeleted {len(existing)} Edge IQ agents.")
        return 0

    # Specialists first: the orchestrator's connected_agent tools must resolve
    # to agents that already exist.
    ordered = sorted(plan, key=lambda p: p["metadata"]["role"] != "specialist")

    for item in ordered:
        name = item["name"]
        tools = []
        for tool in item["tools"]:
            if tool["type"] == "mcp":
                if "localhost" in tool["server_url"] or "127.0.0.1" in tool["server_url"]:
                    continue  # Foundry cannot reach a local gateway; attach after MCP is deployed.
                # Foundry requires labels matching ^[a-zA-Z0-9_]+$; approval
                # mode is set per run, not on the definition.
                tools.append({
                    "type": "mcp",
                    "server_label": tool["server_label"].replace("-", "_"),
                    "server_url": tool["server_url"],
                    "allowed_tools": [],
                })
            elif tool["type"] == "connected_agent":
                target = existing.get(f"edgeiq-{tool['name']}")
                if target:
                    tools.append({
                        "type": "connected_agent",
                        "connected_agent": {
                            "id": target,
                            "name": tool["name"].replace("-", "_"),
                            "description": tool["description"][:500],
                        },
                    })
        payload = {
            "model": item["model"],
            "name": name,
            "description": item["description"][:512],
            "instructions": item["instructions"],
            "temperature": item["temperature"],
            "tools": tools,
            "metadata": {k: str(v)[:512] for k, v in item["metadata"].items()},
        }
        if name in existing:
            agents_api.update_agent(agent_id=existing[name], **payload)
            print(f"  updated {name}  ({existing[name]})")
        else:
            created = agents_api.create_agent(**payload)
            existing[name] = created.id
            print(f"  created {name}  ({created.id})")

    print(f"\nDone. {len(ordered)} agents registered.")
    print("Set AZURE_AI_FOUNDRY_ORCHESTRATOR_AGENT_ID to the orchestrator id "
          "to have the API bind to the cloud-hosted topology.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
