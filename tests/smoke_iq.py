"""
Edge IQ IQ-layer smoke test - proves Foundry IQ retrieval, Fabric IQ ontology
resolution and the unified context assembly all work on the demo corpus.

    python tests/smoke_iq.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("EDGEIQ_DEMO_MODE", "true")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from api.python.config import get_settings  # noqa: E402
from api.python.iq.fabric_iq import FabricIQ  # noqa: E402
from api.python.iq.foundry_iq import FoundryIQ  # noqa: E402
from api.python.iq.work_iq import WorkIQ  # noqa: E402
from api.python.iq.context import build_unified_context  # noqa: E402

RETRIEVAL_CASES = [
    ("What do we do when chlorine residual drops below the action level?", "SOP-CL2-001"),
    ("How do I interpret pump vibration against ISO 10816?", "SOP-ROT-001"),
    ("Rising minimum night flow in a district metered area", "SOP-DMA-001"),
    ("A gateway has gone offline - what is the triage order?", "SOP-EDGE-001"),
    ("How is work order priority determined?", "REF-PRIORITY-001"),
    ("What is the EPA turbidity limit?", "REG-EPA-001"),
    ("Configuration drift on a module twin", "SOP-EDGE-002"),
    ("How do we calculate specific energy consumption?", "SOP-NRG-001"),
]

ENTITY_CASES = [
    ("pump", "Asset"), ("gateway", "EdgeDevice"), ("night flow", "DmaFlow"),
    ("chlorine", "WaterQuality"), ("work order", "WorkOrder"), ("site", "Site"),
]


async def main() -> int:
    failures: list[str] = []
    settings = get_settings()
    print("EDGE IQ - IQ LAYER SMOKE TEST (demo mode)\n")

    # ---------------------------------------------------------- Foundry IQ
    print("=" * 70)
    print("1. FOUNDRY IQ - governed knowledge retrieval")
    print("=" * 70)
    foundry = FoundryIQ(settings.foundry_iq, None)
    corpus = foundry._load_local()
    print(f"  corpus: {len(corpus)} documents")
    if len(corpus) < 15:
        failures.append(f"corpus too small: {len(corpus)}")

    for query, expected_id in RETRIEVAL_CASES:
        chunks = await foundry.retrieve(query, top_k=3)
        ids = [c.citation_id for c in chunks]
        mark = "PASS" if expected_id in ids else "FAIL"
        if mark == "FAIL":
            failures.append(f"retrieval: {query!r} -> {ids}, wanted {expected_id}")
        print(f"  [{mark}] {query[:50]:<50} -> {', '.join(ids[:3])}")

    # governance: a 'public' caller must not see the confidential runbook
    public_hits = await foundry.retrieve(
        "edge device certificate security baseline", top_k=10, max_classification="public"
    )
    leaked = [c.citation_id for c in public_hits if c.classification == "confidential"]
    print(f"\n  governance filter (public clearance): "
          f"{len(public_hits)} hits, confidential leaked = {leaked}")
    if leaked:
        failures.append(f"classification leak: {leaked}")

    internal_hits = await foundry.retrieve(
        "edge device certificate security baseline", top_k=10, max_classification="confidential"
    )
    got_conf = any(c.classification == "confidential" for c in internal_hits)
    print(f"  governance filter (confidential clearance): confidential returned = {got_conf}")
    if not got_conf:
        failures.append("confidential clearance did not return confidential doc")

    # ----------------------------------------------------------- Fabric IQ
    print("\n" + "=" * 70)
    print("2. FABRIC IQ - business ontology")
    print("=" * 70)
    fabric = FabricIQ(settings.fabric_iq, None)
    onto = fabric.ontology
    print(f"  ontology: {onto.get('name')} v{onto.get('version')}")
    print(f"  entities: {len(onto.get('entities', []))}  "
          f"measures: {len(onto.get('measures', []))}  "
          f"relationships: {len(onto.get('relationships', []))}")
    if not onto.get("entities"):
        failures.append("ontology has no entities")

    for term, expected in ENTITY_CASES:
        resolved = fabric.resolve_entity(term)
        got = resolved.get("name") if resolved else None
        mark = "PASS" if got == expected else "FAIL"
        if mark == "FAIL":
            failures.append(f"resolve_entity({term!r}) -> {got}, wanted {expected}")
        print(f"  [{mark}] {term:<14} -> {got}")

    print("\n  describe_ontology() preview:")
    for line in fabric.describe_ontology().splitlines()[:6]:
        print(f"    {line}")

    # ------------------------------------------------------------- Work IQ
    print("\n" + "=" * 70)
    print("3. WORK IQ - M365 signals (placeholder until wired)")
    print("=" * 70)
    work = WorkIQ(settings.work_iq, None)
    signals = await work.search("pump WTP-01-PUMP-003", user_assertion=None)
    print(f"  enabled={settings.work_iq.enabled}  configured={work.configured}")
    print(f"  result: {str(signals.to_dict())[:300]}")

    # ---------------------------------------------------- Unified context
    print("\n" + "=" * 70)
    print("4. UNIFIED CONTEXT LAYER")
    print("=" * 70)
    ctx = await build_unified_context(
        "Why is vibration rising on WTP-01-PUMP-003?",
        foundry_iq=foundry, fabric_iq=fabric, work_iq=work,
        user_assertion=None,
    )
    print(f"  layer status: {ctx.layer_status}")
    block = ctx.to_prompt_block()
    print(f"  prompt block: {len(block)} chars")
    print("  ---")
    for line in block.splitlines()[:18]:
        print(f"  {line}")
    if "error" in str(ctx.layer_status.get("foundry_iq", "")):
        failures.append("foundry_iq layer errored")
    if not block.strip():
        failures.append("unified context produced an empty prompt block")

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL IQ LAYER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
