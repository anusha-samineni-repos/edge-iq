"""
Edge IQ smoke test - exercises all five MCP servers against the demo dataset.

    python tests/smoke_mcp.py

Prints one representative answer per use case so you can confirm the data,
the tools and the domain logic all line up before deploying.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.mcp_servers.compat import tool_fn  # noqa: E402
from src.mcp_servers.servers import (  # noqa: E402
    asset_registry,
    edge_fleet,
    historian,
    water_quality,
    work_order,
)


def show(title: str, payload, limit: int = 900) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    text = json.dumps(payload, indent=1, default=str)
    print(text[:limit] + ("..." if len(text) > limit else ""))


async def main() -> int:
    failures = []

    async def check(name, coro, assertion=None):
        try:
            result = await coro
            show(name, result)
            if assertion and not assertion(result):
                failures.append(f"{name}: assertion failed")
            return result
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            print(f"\n!! {name} raised: {exc}")
            return None

    print("EDGE IQ MCP SMOKE TEST")

    # UC5 - fleet
    await check(
        "UC5 edge-fleet.get_fleet_summary",
        tool_fn(edge_fleet.get_fleet_summary)(),
        lambda r: r["ok"] and r["data"]["totalDevices"] > 0,
    )
    await check(
        "UC5 edge-fleet.get_certificate_status (30d)",
        tool_fn(edge_fleet.get_certificate_status)(days_ahead=30),
        lambda r: r["ok"],
    )
    await check(
        "UC5 edge-fleet.get_module_twin (config drift)",
        tool_fn(edge_fleet.get_module_twin)("WTP-01-GW-001"),
        lambda r: r["driftCount"] > 0,
    )

    # UC1 - asset health
    await check(
        "UC1 historian.get_statistics vibration",
        tool_fn(historian.get_statistics)("WTP-01-PUMP-003", "vibration_mm_s", 336),
        lambda r: r["ok"] and r["data"]["trend"] == "rising",
    )
    await check(
        "UC1 asset-registry.get_vibration_limits",
        tool_fn(asset_registry.get_vibration_limits)("WTP-01-PUMP-003"),
        lambda r: r["ok"] and r["data"]["isoBand"] == "medium",
    )
    await check(
        "UC1 asset-registry.get_failure_modes (pump)",
        tool_fn(asset_registry.get_failure_modes)("pump"),
        lambda r: r["count"] > 0,
    )

    # UC2 - water quality
    await check(
        "UC2 water-quality.check_compliance RES-03",
        tool_fn(water_quality.check_compliance)("RES-03", hours=336),
        lambda r: r["ok"],
    )
    await check(
        "UC2 water-quality.assess_sensor_validity (flatline)",
        tool_fn(water_quality.assess_sensor_validity)("RES-07-CL2-001", "chlorine_mg_l", 48),
        lambda r: r["ok"],
    )

    # UC3 - leak detection
    await check(
        "UC3 water-quality.get_dma_flow DMA-14",
        tool_fn(water_quality.get_dma_flow)("DMA-14", 336),
        lambda r: r["ok"] and r["data"]["excessMnfM3h"] is not None,
    )
    await check(
        "UC3 water-quality.get_pressure_profile DMA-14",
        tool_fn(water_quality.get_pressure_profile)("DMA-14", 168),
        lambda r: r["ok"],
    )
    await check("UC3 water-quality.calculate_nrw", tool_fn(water_quality.calculate_nrw)("DMA-14"))

    # UC4 - energy
    await check(
        "UC4 historian.compare_devices power",
        tool_fn(historian.compare_devices)(["PS-04-PUMP-001", "PS-04-PUMP-002"], "power_kw", 336),
        lambda r: r["ok"],
    )

    # Cross-cutting - work orders
    await check(
        "WO work-order.calculate_priority",
        tool_fn(work_order.calculate_priority)("critical", "availability"),
        lambda r: r["data"]["priority"] == "P2",
    )
    await check(
        "WO work-order.create_work_order (unapproved -> proposal)",
        tool_fn(work_order.create_work_order)(
            asset_id="WTP-01-PUMP-003", site_id="WTP-01",
            title="Replace drive-end bearing", description="Vibration in ISO zone D.",
            priority="P2", failure_mode="Bearing wear", estimated_hours=8,
            required_parts=["BRG-6314-C3"],
        ),
        lambda r: r["committed"] is False and r["approvalRequired"] is True,
    )
    await check(
        "WO work-order.estimate_cost",
        tool_fn(work_order.estimate_cost)(8.0, ["BRG-6314-C3", "SEAL-MECH-75"], 12.0),
        lambda r: r["data"]["totalCost"] > 0,
    )
    await check(
        "Data quality guard",
        tool_fn(historian.get_data_quality)("WTP-01-PUMP-003", 24),
        lambda r: r["ok"],
    )

    print(f"\n{'=' * 70}")
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL MCP SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
