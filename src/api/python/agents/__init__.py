"""
Edge IQ agent roster.

Eight agents in total:

  Orchestrator (1)
    edgeiq-orchestrator   Plans, routes, delegates, synthesizes, guards.

  Use-case specialists (5) - one per water utility edge use case
    asset-health          UC1  Predictive maintenance of pumps/blowers/motors
    water-quality         UC2  Distribution water quality & compliance
    leak-detection        UC3  Non-revenue water, DMA leak & burst localisation
    energy-optimizer      UC4  Pump scheduling / energy & carbon optimisation
    fleet-operations      UC5  Edge fleet health, config drift, OTA, security

  Cross-cutting specialists (2)
    knowledge-navigator   Foundry IQ deep retrieval, SOP/regulatory Q&A
    maintenance-planner   Work order authoring + Work IQ scheduling
"""

from .registry import (
    AGENT_SPECS,
    ORCHESTRATOR,
    SPECIALIST_SPECS,
    AgentSpec,
    get_agent_spec,
    list_agent_specs,
)
from .factory import build_agents, build_orchestrator
from .routing import RouteDecision, agent_by_name, describe_routing_table, route

__all__ = [
    "AGENT_SPECS",
    "ORCHESTRATOR",
    "SPECIALIST_SPECS",
    "AgentSpec",
    "get_agent_spec",
    "list_agent_specs",
    "build_agents",
    "build_orchestrator",
    "route",
    "RouteDecision",
    "describe_routing_table",
    "agent_by_name",
]
