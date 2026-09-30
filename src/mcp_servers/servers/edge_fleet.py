"""
MCP server: edge-fleet  (UC5 - Edge fleet operations & security)

Tools for the physical/logical health of the IoT Edge estate itself: gateways,
modules, module twins, certificates, connectivity and OTA deployments.

This is deliberately separate from ``historian``: when telemetry goes missing the
first question is always "is the device broken or is the water system broken?",
and that triage needs fleet facts, not process values.
"""

from __future__ import annotations

from ..compat import build_server

from ..datasource import backend, cosmos_query, filter_rows, load_csv, ok, within_hours

mcp = build_server("edgeiq-edge-fleet")


async def _devices() -> list[dict]:
    if backend() == "azure":
        rows = await cosmos_query("assets", "SELECT * FROM c WHERE c.docType = 'device'")
        if rows:
            return rows
    return load_csv("devices")


@mcp.tool()
async def list_devices(
    site_id: str | None = None,
    device_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    """
    List IoT Edge devices and their current fleet status.

    site_id      e.g. WTP-01, PS-04, DMA-14
    device_type  gateway | plc | sensor | analyzer | flowmeter | vfd
    status       online | offline | degraded | provisioning
    """
    rows = filter_rows(
        await _devices(), limit=limit, siteId=site_id, deviceType=device_type, status=status
    )
    return ok(rows, filters={"siteId": site_id, "deviceType": device_type, "status": status})


@mcp.tool()
async def get_device(device_id: str) -> dict:
    """Full record for one device: firmware, modules, certificate expiry, last contact."""
    rows = filter_rows(await _devices(), limit=1, deviceId=device_id)
    if not rows:
        return ok(None, message=f"No device '{device_id}' in the registry.")
    return ok(rows[0])


@mcp.tool()
async def get_module_twin(device_id: str, module_id: str | None = None) -> dict:
    """
    Desired vs reported properties for edge modules on a device.

    Use this to detect configuration drift - the single most common cause of
    'the edge box is online but the data looks wrong'.
    """
    rows = filter_rows(load_csv("module_twins"), limit=25, deviceId=device_id, moduleId=module_id)
    drifted = [
        r for r in rows if str(r.get("desiredValue")) != str(r.get("reportedValue"))
    ]
    return ok(
        rows,
        driftCount=len(drifted),
        drifted=[
            {
                "moduleId": r.get("moduleId"),
                "property": r.get("property"),
                "desired": r.get("desiredValue"),
                "reported": r.get("reportedValue"),
            }
            for r in drifted
        ],
    )


@mcp.tool()
async def get_connectivity_history(device_id: str, hours: int = 72) -> dict:
    """Connect/disconnect events and uptime percentage for a device."""
    rows = within_hours(
        filter_rows(load_csv("connectivity_events"), limit=2000, deviceId=device_id), hours
    )
    disconnects = [r for r in rows if r.get("event") == "disconnected"]
    uptime = round(
        100.0 - min(100.0, sum(float(r.get("durationMinutes") or 0) for r in disconnects) / (hours * 60) * 100),
        2,
    )
    return ok(rows, deviceId=device_id, windowHours=hours, disconnectCount=len(disconnects), uptimePercent=uptime)


@mcp.tool()
async def get_certificate_status(site_id: str | None = None, days_ahead: int = 90) -> dict:
    """
    Devices whose X.509 identity or TLS certificates expire within *days_ahead*.

    Certificate expiry is the number-one cause of fleet-wide edge outages and is
    100% preventable, so this is surfaced as a first-class tool.
    """
    rows = filter_rows(await _devices(), limit=500, siteId=site_id)
    expiring = [
        r for r in rows
        if r.get("certDaysToExpiry") is not None and float(r["certDaysToExpiry"]) <= days_ahead
    ]
    expiring.sort(key=lambda r: float(r.get("certDaysToExpiry") or 9999))
    return ok(
        expiring,
        daysAhead=days_ahead,
        critical=[r["deviceId"] for r in expiring if float(r.get("certDaysToExpiry") or 9999) <= 14],
    )


@mcp.tool()
async def get_deployment_status(deployment_id: str | None = None) -> dict:
    """OTA / edge deployment rollout status: targeted, applied, reporting success."""
    rows = filter_rows(load_csv("deployments"), limit=50, deploymentId=deployment_id)
    return ok(rows)


@mcp.tool()
async def get_fleet_summary(site_id: str | None = None) -> dict:
    """One-shot fleet health rollup - use this before drilling into single devices."""
    rows = filter_rows(await _devices(), limit=1000, siteId=site_id)
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row.get("status", "unknown")] = by_status.get(row.get("status", "unknown"), 0) + 1
    stale = [r["deviceId"] for r in rows if (r.get("minutesSinceLastMessage") or 0) > 60]
    cert_risk = [
        r["deviceId"] for r in rows
        if r.get("certDaysToExpiry") is not None and float(r["certDaysToExpiry"]) <= 30
    ]
    return ok(
        {
            "totalDevices": len(rows),
            "byStatus": by_status,
            "staleTelemetry": stale,
            "certificateRisk": cert_risk,
            "healthScore": round(100 * by_status.get("online", 0) / max(len(rows), 1), 1),
        },
        siteId=site_id,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
