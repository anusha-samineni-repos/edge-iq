"""
Edge IQ API smoke test - boots the orchestrator in demo mode and exercises
routing, the unified context layer, health and a full chat turn.

    python tests/smoke_api.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("EDGEIQ_DEMO_MODE", "true")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from api.python.agents import ORCHESTRATOR, SPECIALIST_SPECS, route  # noqa: E402
from api.python.orchestrator import get_orchestrator  # noqa: E402

ROUTING_CASES = [
    ("Why is the vibration on WTP-01-PUMP-003 climbing?", "asset-health"),
    ("Is RES-03 chlorine still compliant?", "water-quality"),
    ("DMA-14 night flow looks high - do we have a leak?", "leak-detection"),
    ("Which pump is wasting the most energy at peak tariff?", "energy-optimizer"),
    ("WELL-09-GW-001 has stopped reporting", "fleet-operations"),
    ("What does the SOP say about chlorine residual sampling?", "knowledge-navigator"),
    ("Create a work order for the pump bearing", "maintenance-planner"),
    ("I'm not receiving any data from WTP-01-GW-001", "fleet-operations"),
    ("Should we issue a boil water notice?", "water-quality"),
]


async def main() -> int:
    failures: list[str] = []
    print("EDGE IQ API SMOKE TEST (demo mode)\n")

    # 1. Routing -------------------------------------------------------
    print("=" * 70)
    print("1. DETERMINISTIC ROUTING")
    print("=" * 70)
    for question, expected in ROUTING_CASES:
        decision = route(question)
        top = decision.agents[0] if decision.agents else "<none>"
        mark = "PASS" if top == expected else "FAIL"
        if mark == "FAIL":
            failures.append(f"routing: {question!r} -> {top}, expected {expected}")
        print(f"  [{mark}] {question[:52]:<52} -> {top:<20} "
              f"conf={decision.confidence:.2f} {decision.strategy}")

    # 2. Startup -------------------------------------------------------
    print("\n" + "=" * 70)
    print("2. ORCHESTRATOR STARTUP")
    print("=" * 70)
    orch = get_orchestrator()
    await orch.startup()
    health = orch.health()
    print(json.dumps(health, indent=1, default=str)[:1400])
    if health.get("status") not in {"ok", "ready", "degraded"}:
        failures.append(f"health status = {health.get('status')}")

    # 3. Agent inventory ----------------------------------------------
    print("\n" + "=" * 70)
    print("3. AGENT INVENTORY")
    print("=" * 70)
    print(f"  orchestrator: {ORCHESTRATOR.name}")
    for spec in SPECIALIST_SPECS:
        print(f"  - {spec.name:<22} uc={spec.use_case:<4} "
              f"mcp={','.join(spec.mcp_servers) or '-'} iq={','.join(spec.iq_layers)}")
    if len(SPECIALIST_SPECS) != 7:
        failures.append(f"expected 7 specialists, found {len(SPECIALIST_SPECS)}")

    # 4. A full chat turn ---------------------------------------------
    print("\n" + "=" * 70)
    print("4. END-TO-END TURN (streamed)")
    print("=" * 70)
    events: list[str] = []
    text_parts: list[str] = []
    async for event in orch.ask_stream(
        "Why is vibration rising on WTP-01-PUMP-003 and what should we do?",
        conversation_id="smoke-001",
    ):
        events.append(event.get("type", "?"))
        if event.get("type") == "delta":
            text_parts.append(event.get("text", ""))
        elif event.get("type") == "route":
            r = event.get("route", {})
            print(f"  route -> {r.get('agents')} ({r.get('strategy')}) conf={r.get('confidence')}")
        elif event.get("type") == "error":
            failures.append(f"turn error: {event.get('message')}")
    print(f"  events: {', '.join(dict.fromkeys(events))}")
    answer = "".join(text_parts)
    print(f"  answer ({len(answer)} chars):\n  {answer[:500]}")
    if "final" not in events:
        failures.append("no final event emitted")

    # 5. Memory --------------------------------------------------------
    print("\n" + "=" * 70)
    print("5. CONVERSATION MEMORY")
    print("=" * 70)
    history = await orch.store.get_history("smoke-001")
    print(f"  turns stored: {len(history)}")
    traces = await orch.store.get_traces("smoke-001")
    print(f"  traces stored: {len(traces)}")
    if not history:
        failures.append("conversation memory did not persist the turn")

    await orch.shutdown()

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL API SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
