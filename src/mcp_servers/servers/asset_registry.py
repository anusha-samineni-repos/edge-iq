"""
MCP server: asset-registry  (UC1 / UC4 - what the asset IS)

Nameplate data, hierarchy, criticality, maintenance history and failure modes.
The historian says what an asset is *doing*; this server says what it *is* and
what "normal" means for it. Without this, a 4.2 mm/s vibration reading is
meaningless - it is fine on a large motor and a failure on a small one.
"""

from __future__ import annotations

from ..compat import build_server

from ..datasource import backend, cosmos_query, filter_rows, load_csv, ok

mcp = build_server("edgeiq-asset-registry")

# ISO 10816-3 vibration velocity zone boundaries (mm/s RMS), rigid mounting.
ISO_10816_ZONES = {
    "small": {"A_B": 1.4, "B_C": 2.8, "C_D": 4.5, "note": "<= 15 kW"},
    "medium": {"A_B": 2.3, "B_C": 4.5, "C_D": 7.1, "note": "15-75 kW"},
    "large": {"A_B": 2.8, "B_C": 5.6, "C_D": 9.0, "note": "> 75 kW, rigid foundation"},
}


async def _assets() -> list[dict]:
    if backend() == "azure":
        rows = await cosmos_query("assets", "SELECT * FROM c")
        if rows:
            return rows
    return load_csv("assets")


@mcp.tool()
async def get_asset(asset_id: str) -> dict:
    """Nameplate record: manufacturer, model, rating, install date, duty, criticality."""
    rows = filter_rows(await _assets(), limit=1, assetId=asset_id)
    if not rows:
        rows = filter_rows(await _assets(), limit=1, deviceId=asset_id)
    if not rows:
        return ok(None, message=f"No asset '{asset_id}' in the registry.")
    return ok(rows[0])


@mcp.tool()
async def list_assets(
    site_id: str | None = None,
    asset_class: str | None = None,
    criticality: str | None = None,
    limit: int = 50,
) -> dict:
    """
    List assets.

    asset_class  pump | blower | motor | valve | analyzer | flowmeter | prv
    criticality  critical | high | medium | low
    """
    rows = filter_rows(
        await _assets(), limit=limit, siteId=site_id, assetClass=asset_class, criticality=criticality
    )
    return ok(rows)


@mcp.tool()
async def get_asset_hierarchy(site_id: str) -> dict:
    """Site -> process area -> asset -> edge device. Use this to reason about blast radius."""
    rows = filter_rows(await _assets(), limit=500, siteId=site_id)
    tree: dict[str, list[dict]] = {}
    for row in rows:
        area = row.get("processArea", "unassigned")
        tree.setdefault(area, []).append(
            {
                "assetId": row.get("assetId"),
                "assetClass": row.get("assetClass"),
                "deviceId": row.get("deviceId"),
                "criticality": row.get("criticality"),
                "dutyRole": row.get("dutyRole"),
            }
        )
    return ok(tree, siteId=site_id, processAreas=list(tree))


@mcp.tool()
async def get_vibration_limits(asset_id: str) -> dict:
    """
    ISO 10816-3 alarm/trip bands for this specific asset, based on its kW rating.

    Always call this before judging a vibration reading.
    """
    rows = filter_rows(await _assets(), limit=1, assetId=asset_id)
    if not rows:
        return ok(None, message=f"No asset '{asset_id}'.")
    asset = rows[0]
    kw = float(asset.get("ratedPowerKw") or 0)
    band = "small" if kw <= 15 else "medium" if kw <= 75 else "large"
    zones = ISO_10816_ZONES[band]
    return ok(
        {
            "assetId": asset_id,
            "ratedPowerKw": kw,
            "isoBand": band,
            "zones": zones,
            "interpretation": {
                "A": f"< {zones['A_B']} mm/s - newly commissioned condition",
                "B": f"{zones['A_B']}-{zones['B_C']} mm/s - acceptable for long-term operation",
                "C": f"{zones['B_C']}-{zones['C_D']} mm/s - unsatisfactory, plan intervention",
                "D": f"> {zones['C_D']} mm/s - damaging, act now",
            },
            "standard": "ISO 10816-3 / ISO 20816-3, velocity RMS, rigid mounting",
        }
    )


@mcp.tool()
async def get_maintenance_history(asset_id: str, limit: int = 20) -> dict:
    """Closed work orders for this asset: what failed, what was done, parts, downtime."""
    rows = filter_rows(load_csv("workorders"), limit=200, assetId=asset_id)
    rows = [r for r in rows if r.get("status") in ("closed", "completed")]
    rows.sort(key=lambda r: str(r.get("completedAt")), reverse=True)
    rows = rows[:limit]
    failure_modes: dict[str, int] = {}
    for row in rows:
        mode = row.get("failureMode")
        if mode:
            failure_modes[mode] = failure_modes.get(mode, 0) + 1
    total_downtime = sum(float(r.get("downtimeHours") or 0) for r in rows)
    return ok(
        rows,
        assetId=asset_id,
        recurringFailureModes=sorted(failure_modes.items(), key=lambda kv: kv[1], reverse=True),
        totalDowntimeHours=round(total_downtime, 1),
    )


@mcp.tool()
async def get_failure_modes(asset_class: str) -> dict:
    """
    FMEA table for an asset class: mode, detectable signature, typical lead time.

    This is how an agent turns "vibration is rising at 1x running speed" into
    "probable imbalance or coupling misalignment" rather than a vague warning.
    """
    rows = filter_rows(load_csv("failure_modes"), limit=100, assetClass=asset_class)
    return ok(rows, assetClass=asset_class)


@mcp.tool()
async def get_spare_parts(asset_id: str) -> dict:
    """Bill of materials and current stock for an asset - drives realistic work-order dates."""
    rows = filter_rows(load_csv("spare_parts"), limit=50, assetId=asset_id)
    shortages = [r for r in rows if (r.get("onHand") or 0) < (r.get("minStock") or 0)]
    return ok(rows, assetId=asset_id, shortages=shortages)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
