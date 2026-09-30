"""
MCP server: historian  (UC1 / UC3 / UC4 - time-series process data)

Reads edge telemetry from the Fabric Eventhouse (KQL) in dev/prod, or the bundled
hourly CSV corpus in demo mode. This is the numeric evidence layer: vibration,
pressure, flow, power, temperature, level.

Every tool returns units and the observation window so agents cannot quote a
value without being able to attribute it.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from ..compat import build_server

from ..datasource import backend, filter_rows, kql_query, load_csv, ok, within_hours

mcp = build_server("edgeiq-historian")

UNITS = {
    "vibration_mm_s": "mm/s RMS",
    "bearing_temp_c": "degC",
    "motor_current_a": "A",
    "suction_pressure_bar": "bar",
    "discharge_pressure_bar": "bar",
    "flow_m3h": "m3/h",
    "power_kw": "kW",
    "level_m": "m",
    "chlorine_mg_l": "mg/L",
    "turbidity_ntu": "NTU",
    "ph": "pH",
}


async def _series(device_id: str, tag: str, hours: int) -> list[dict]:
    if backend() == "azure":
        rows = await kql_query(
            f"EdgeTelemetry | where DeviceId == '{device_id}' "
            f"| where Timestamp > ago({hours}h) "
            f"| project Timestamp, DeviceId, Tag='{tag}', Value=todouble({tag}) "
            f"| order by Timestamp asc | take 5000"
        )
        if rows:
            return [
                {"timestamp": r.get("Timestamp"), "deviceId": r.get("DeviceId"), "value": r.get("Value")}
                for r in rows
            ]
    rows = within_hours(filter_rows(load_csv("telemetry_hourly"), limit=20000, deviceId=device_id), hours)
    return [
        {"timestamp": r.get("timestamp"), "deviceId": r.get("deviceId"), "value": r.get(tag)}
        for r in rows
        if r.get(tag) is not None
    ]


@mcp.tool()
async def list_tags(device_id: str) -> dict:
    """Which telemetry tags this device actually publishes, with units."""
    rows = filter_rows(load_csv("telemetry_hourly"), limit=1, deviceId=device_id)
    if not rows:
        return ok([], message=f"No telemetry found for '{device_id}'.")
    tags = [
        {"tag": k, "unit": UNITS.get(k, "")}
        for k, v in rows[0].items()
        if k not in ("timestamp", "deviceId", "siteId") and v is not None
    ]
    return ok(tags, deviceId=device_id)


@mcp.tool()
async def get_timeseries(device_id: str, tag: str, hours: int = 24, max_points: int = 200) -> dict:
    """
    Raw (hourly) time series for one tag.

    tag    e.g. vibration_mm_s, discharge_pressure_bar, flow_m3h, power_kw
    hours  look-back window
    """
    points = await _series(device_id, tag, hours)
    if len(points) > max_points:
        step = len(points) // max_points + 1
        points = points[::step]
    return ok(points, deviceId=device_id, tag=tag, unit=UNITS.get(tag, ""), windowHours=hours)


@mcp.tool()
async def get_statistics(device_id: str, tag: str, hours: int = 168) -> dict:
    """Min / max / mean / stdev / p95 and trend direction for a tag."""
    points = [p["value"] for p in await _series(device_id, tag, hours) if p["value"] is not None]
    if not points:
        return ok(None, message=f"No '{tag}' data for '{device_id}' in the last {hours}h.")
    ordered = sorted(points)
    half = len(points) // 2 or 1
    first_mean = statistics.fmean(points[:half])
    last_mean = statistics.fmean(points[-half:])
    delta = last_mean - first_mean
    trend = "rising" if delta > abs(first_mean) * 0.05 else "falling" if delta < -abs(first_mean) * 0.05 else "stable"
    return ok(
        {
            "count": len(points),
            "min": round(min(points), 3),
            "max": round(max(points), 3),
            "mean": round(statistics.fmean(points), 3),
            "stdev": round(statistics.pstdev(points), 3) if len(points) > 1 else 0.0,
            "p95": round(ordered[int(len(ordered) * 0.95) - 1], 3),
            "trend": trend,
            "changePercent": round(delta / first_mean * 100, 1) if first_mean else 0.0,
        },
        deviceId=device_id,
        tag=tag,
        unit=UNITS.get(tag, ""),
        windowHours=hours,
    )


@mcp.tool()
async def detect_anomalies(device_id: str, tag: str, hours: int = 168, sigma: float = 3.0) -> dict:
    """
    Flag points beyond *sigma* standard deviations of the window mean.

    Deliberately simple and explainable - operators must be able to check the
    arithmetic. Model-based scoring belongs in the Fabric notebook, not here.
    """
    points = await _series(device_id, tag, hours)
    values = [p["value"] for p in points if p["value"] is not None]
    if len(values) < 8:
        return ok([], message="Not enough data to assess anomalies.")
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) or 1e-9
    anomalies = [
        {**p, "zScore": round((p["value"] - mean) / sd, 2)}
        for p in points
        if p["value"] is not None and abs(p["value"] - mean) > sigma * sd
    ]
    return ok(
        anomalies,
        deviceId=device_id,
        tag=tag,
        unit=UNITS.get(tag, ""),
        mean=round(mean, 3),
        stdev=round(sd, 3),
        sigma=sigma,
        windowHours=hours,
    )


@mcp.tool()
async def compare_devices(device_ids: list[str], tag: str, hours: int = 168) -> dict:
    """
    Side-by-side statistics for peer assets (e.g. duty vs standby pumps).

    Peer comparison is the fastest way to separate 'this asset is degrading'
    from 'the whole site is running differently today'.
    """
    out = []
    for device_id in device_ids[:10]:
        values = [p["value"] for p in await _series(device_id, tag, hours) if p["value"] is not None]
        if not values:
            out.append({"deviceId": device_id, "status": "no data"})
            continue
        out.append(
            {
                "deviceId": device_id,
                "mean": round(statistics.fmean(values), 3),
                "max": round(max(values), 3),
                "count": len(values),
            }
        )
    rated = [o for o in out if "mean" in o]
    if rated:
        fleet_mean = statistics.fmean(o["mean"] for o in rated)
        for o in rated:
            o["deviationFromPeersPercent"] = round((o["mean"] - fleet_mean) / fleet_mean * 100, 1) if fleet_mean else 0.0
    return ok(out, tag=tag, unit=UNITS.get(tag, ""), windowHours=hours)


@mcp.tool()
async def get_alarms(
    device_id: str | None = None,
    site_id: str | None = None,
    severity: str | None = None,
    hours: int = 72,
) -> dict:
    """Active and recent alarms. severity: critical | high | medium | low"""
    rows = within_hours(
        filter_rows(load_csv("alarms"), limit=500, deviceId=device_id, siteId=site_id, severity=severity),
        hours,
        field="raisedAt",
    )
    rows.sort(key=lambda r: str(r.get("raisedAt")), reverse=True)
    active = [r for r in rows if r.get("state") == "active"]
    return ok(rows, activeCount=len(active), windowHours=hours)


@mcp.tool()
async def get_data_quality(device_id: str, hours: int = 24) -> dict:
    """
    Expected vs received sample counts, gap list and staleness.

    Call this BEFORE concluding anything from a value - a 'perfect' reading from
    a frozen sensor is the most dangerous data in a water utility.
    """
    points = within_hours(filter_rows(load_csv("telemetry_hourly"), limit=20000, deviceId=device_id), hours)
    expected = hours
    received = len(points)
    last_ts = max((str(p.get("timestamp")) for p in points), default=None)
    stale_minutes = None
    if last_ts:
        try:
            ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            stale_minutes = int((datetime.now(timezone.utc) - ts) / timedelta(minutes=1))
        except ValueError:
            pass
    completeness = round(received / expected * 100, 1) if expected else 0.0
    return ok(
        {
            "expectedSamples": expected,
            "receivedSamples": received,
            "completenessPercent": completeness,
            "lastTimestamp": last_ts,
            "staleMinutes": stale_minutes,
            "verdict": (
                "healthy" if completeness >= 95 else "degraded" if completeness >= 70 else "unreliable"
            ),
        },
        deviceId=device_id,
        windowHours=hours,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
