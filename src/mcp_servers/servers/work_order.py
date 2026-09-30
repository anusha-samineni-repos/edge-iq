"""
MCP server: work-order  (cross-cutting - the action layer)

The only server that can change the world. Every mutating tool is gated:
``create_work_order`` and friends require ``approved=True``, and without it they
return a *proposal* rather than performing the action.

That is deliberate. A water utility cannot have an LLM silently dispatching
crews or taking a treatment train offline. The agent's job is to produce a
complete, defensible proposal; a human presses go.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from ..compat import build_server

from ..datasource import backend, filter_rows, load_csv, ok

mcp = build_server("edgeiq-work-order")

# Criticality x consequence -> priority and target response, per the utility's
# maintenance policy (also published in the Foundry IQ knowledge base).
PRIORITY_MATRIX = {
    ("critical", "safety"): ("P1", 4),
    ("critical", "compliance"): ("P1", 8),
    ("critical", "availability"): ("P2", 24),
    ("high", "safety"): ("P1", 8),
    ("high", "compliance"): ("P2", 24),
    ("high", "availability"): ("P3", 72),
    ("medium", "availability"): ("P3", 120),
    ("medium", "efficiency"): ("P4", 336),
    ("low", "efficiency"): ("P4", 720),
}


def _write_enabled() -> bool:
    return os.getenv("EDGEIQ_ALLOW_WRITES", "false").lower() == "true"


@mcp.tool()
async def list_work_orders(
    site_id: str | None = None,
    asset_id: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    limit: int = 50,
) -> dict:
    """
    List work orders.

    status    open | scheduled | in_progress | closed | completed
    priority  P1 | P2 | P3 | P4
    """
    rows = filter_rows(
        load_csv("workorders"), limit=limit, siteId=site_id, assetId=asset_id,
        status=status, priority=priority,
    )
    rows.sort(key=lambda r: (str(r.get("priority")), str(r.get("createdAt"))))
    return ok(rows)


@mcp.tool()
async def get_work_order(work_order_id: str) -> dict:
    """Full detail for one work order including parts, labour and history."""
    rows = filter_rows(load_csv("workorders"), limit=1, workOrderId=work_order_id)
    if not rows:
        return ok(None, message=f"No work order '{work_order_id}'.")
    return ok(rows[0])


@mcp.tool()
async def calculate_priority(criticality: str, consequence: str) -> dict:
    """
    Apply the maintenance priority matrix.

    criticality  critical | high | medium | low  (from the asset registry)
    consequence  safety | compliance | availability | efficiency

    Agents must use this rather than inventing a priority, so that every work
    order Edge IQ proposes is consistent with the utility's own policy.
    """
    key = (criticality.lower(), consequence.lower())
    priority, hours = PRIORITY_MATRIX.get(key, ("P4", 720))
    due = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return ok(
        {
            "criticality": criticality,
            "consequence": consequence,
            "priority": priority,
            "targetResponseHours": hours,
            "dueBy": due,
            "policy": "Edge IQ maintenance priority matrix v1.0",
        }
    )


@mcp.tool()
async def check_crew_availability(site_id: str, skill: str | None = None, days_ahead: int = 14) -> dict:
    """Crew capacity by day and skill - makes proposed dates realistic, not aspirational."""
    rows = filter_rows(load_csv("crew_availability"), limit=200, siteId=site_id, skill=skill)
    open_slots = [r for r in rows if (r.get("availableHours") or 0) > 0][:days_ahead]
    return ok(open_slots, siteId=site_id, skill=skill, earliest=open_slots[0] if open_slots else None)


@mcp.tool()
async def create_work_order(
    asset_id: str,
    site_id: str,
    title: str,
    description: str,
    priority: str,
    failure_mode: str | None = None,
    estimated_hours: float = 4.0,
    required_parts: list[str] | None = None,
    approved: bool = False,
) -> dict:
    """
    Create a work order. **Requires explicit human approval.**

    Call with approved=False (the default) to return a fully-formed proposal for
    the operator to review. Only call with approved=True after the operator has
    confirmed in the conversation AND EDGEIQ_ALLOW_WRITES is enabled.
    """
    proposal = {
        "workOrderId": f"WO-{uuid.uuid4().hex[:8].upper()}",
        "assetId": asset_id,
        "siteId": site_id,
        "title": title,
        "description": description,
        "priority": priority,
        "failureMode": failure_mode,
        "estimatedHours": estimated_hours,
        "requiredParts": required_parts or [],
        "status": "open",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "createdBy": "edge-iq/maintenance-planner",
    }

    if not approved:
        return ok(
            proposal,
            committed=False,
            approvalRequired=True,
            message=(
                "PROPOSAL ONLY - not submitted. Present this to the operator and "
                "re-call with approved=true once they confirm."
            ),
        )

    if not _write_enabled():
        return ok(
            proposal,
            committed=False,
            approvalRequired=True,
            message=(
                "Approval given but writes are disabled in this environment "
                "(EDGEIQ_ALLOW_WRITES=false). Nothing was submitted."
            ),
        )

    if backend() == "azure":
        try:
            from azure.cosmos.aio import CosmosClient
            from azure.identity.aio import DefaultAzureCredential

            async with DefaultAzureCredential(exclude_interactive_browser_credential=True) as cred:
                async with CosmosClient(os.environ["AZURE_COSMOS_ENDPOINT"], credential=cred) as client:
                    db = client.get_database_client(os.getenv("AZURE_COSMOS_DATABASE", "edgeiq"))
                    container = db.get_container_client(os.getenv("COSMOS_WORKORDERS_CONTAINER", "workorders"))
                    await container.upsert_item({"id": proposal["workOrderId"], **proposal})
            return ok(proposal, committed=True, message="Work order created in Cosmos DB.")
        except Exception as exc:
            return ok(proposal, committed=False, message=f"Write failed: {exc}")

    return ok(proposal, committed=True, message="Work order recorded (demo backend).")


@mcp.tool()
async def update_work_order(
    work_order_id: str,
    status: str | None = None,
    priority: str | None = None,
    notes: str | None = None,
    approved: bool = False,
) -> dict:
    """Update a work order. **Requires explicit human approval** (see create_work_order)."""
    change = {
        "workOrderId": work_order_id,
        "status": status,
        "priority": priority,
        "notes": notes,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    if not approved or not _write_enabled():
        return ok(change, committed=False, approvalRequired=True, message="PROPOSAL ONLY - not submitted.")
    return ok(change, committed=True, message="Work order updated.")


@mcp.tool()
async def assign_work_order(
    work_order_id: str, crew_id: str, scheduled_date: str, approved: bool = False
) -> dict:
    """Assign a work order to a crew. **Requires explicit human approval.**"""
    assignment = {
        "workOrderId": work_order_id,
        "crewId": crew_id,
        "scheduledDate": scheduled_date,
        "assignedAt": datetime.now(timezone.utc).isoformat(),
    }
    if not approved or not _write_enabled():
        return ok(assignment, committed=False, approvalRequired=True, message="PROPOSAL ONLY - not submitted.")
    return ok(assignment, committed=True, message="Work order assigned.")


@mcp.tool()
async def estimate_cost(
    estimated_hours: float, required_parts: list[str] | None = None, downtime_hours: float = 0.0
) -> dict:
    """Labour + parts + production-loss estimate so a proposal carries a business case."""
    labour_rate = float(os.getenv("EDGEIQ_LABOUR_RATE", "95"))
    downtime_rate = float(os.getenv("EDGEIQ_DOWNTIME_COST_PER_HOUR", "1200"))
    parts = load_csv("spare_parts")
    parts_cost = 0.0
    breakdown = []
    for part in required_parts or []:
        match = filter_rows(parts, limit=1, partNumber=part)
        cost = float(match[0].get("unitCost") or 0) if match else 0.0
        parts_cost += cost
        breakdown.append({"partNumber": part, "unitCost": cost, "found": bool(match)})
    labour = estimated_hours * labour_rate
    downtime = downtime_hours * downtime_rate
    return ok(
        {
            "labourCost": round(labour, 2),
            "partsCost": round(parts_cost, 2),
            "downtimeCost": round(downtime, 2),
            "totalCost": round(labour + parts_cost + downtime, 2),
            "currency": os.getenv("EDGEIQ_CURRENCY", "USD"),
            "parts": breakdown,
            "assumptions": {"labourRatePerHour": labour_rate, "downtimeCostPerHour": downtime_rate},
        }
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
