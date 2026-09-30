"""
MCP server: water-quality  (UC2 - distribution water quality & compliance)

Chlorine residual, turbidity, pH, temperature and the regulatory limits that
apply to them, plus DMA hydraulics used by UC3 (leak detection) since both read
the same distribution network model.

Regulatory thresholds are data, not code - they live in ``regulatory_limits.csv``
and in the Foundry IQ knowledge base so a compliance change never needs a
redeploy.
"""

from __future__ import annotations

import statistics

from ..compat import build_server

from ..datasource import filter_rows, load_csv, ok, within_hours

mcp = build_server("edgeiq-water-quality")


@mcp.tool()
async def get_readings(
    device_id: str | None = None,
    site_id: str | None = None,
    parameter: str | None = None,
    hours: int = 24,
) -> dict:
    """
    Water quality analyzer readings.

    parameter  chlorine_mg_l | turbidity_ntu | ph | temperature_c | conductivity_us_cm
    """
    rows = within_hours(
        filter_rows(load_csv("water_quality"), limit=20000, deviceId=device_id, siteId=site_id), hours
    )
    if parameter:
        rows = [
            {"timestamp": r["timestamp"], "deviceId": r["deviceId"], "siteId": r.get("siteId"),
             "parameter": parameter, "value": r.get(parameter)}
            for r in rows
            if r.get(parameter) is not None
        ]
    return ok(rows, windowHours=hours, parameter=parameter)


@mcp.tool()
async def get_regulatory_limits(parameter: str | None = None, jurisdiction: str = "US-EPA") -> dict:
    """
    Applicable limits.

    jurisdiction  US-EPA (SDWA) | UK-DWI | EU-DWD | WHO
    Returns MCL/MRDL, action level, and whether an exceedance is acute (Tier 1).
    """
    rows = filter_rows(
        load_csv("regulatory_limits"), limit=100, parameter=parameter, jurisdiction=jurisdiction
    )
    return ok(rows, jurisdiction=jurisdiction)


@mcp.tool()
async def check_compliance(site_id: str, hours: int = 24, jurisdiction: str = "US-EPA") -> dict:
    """
    Evaluate every measured parameter at a site against its regulatory limit.

    Returns per-parameter status plus an explicit escalation verdict. An acute
    exceedance (Tier 1) requires public notification within 24 hours - the agent
    must surface that, never bury it.
    """
    readings = within_hours(filter_rows(load_csv("water_quality"), limit=20000, siteId=site_id), hours)
    limits = filter_rows(load_csv("regulatory_limits"), limit=100, jurisdiction=jurisdiction)
    limit_by_param = {r["parameter"]: r for r in limits}

    results = []
    escalations = []
    for parameter, limit in limit_by_param.items():
        values = [r[parameter] for r in readings if r.get(parameter) is not None]
        if not values:
            results.append({"parameter": parameter, "status": "no data"})
            continue
        lo = limit.get("minValue")
        hi = limit.get("maxValue")
        breaches = [
            v for v in values
            if (lo is not None and v < float(lo)) or (hi is not None and v > float(hi))
        ]
        status = "compliant" if not breaches else "exceedance"
        entry = {
            "parameter": parameter,
            "unit": limit.get("unit"),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "mean": round(statistics.fmean(values), 3),
            "limitMin": lo,
            "limitMax": hi,
            "samples": len(values),
            "breachCount": len(breaches),
            "status": status,
            "tier": limit.get("tier"),
            "citation": limit.get("citation"),
        }
        results.append(entry)
        if breaches and str(limit.get("tier")) == "1":
            escalations.append(
                {
                    "parameter": parameter,
                    "action": limit.get("requiredAction"),
                    "deadlineHours": limit.get("notificationHours"),
                    "citation": limit.get("citation"),
                }
            )

    return ok(
        results,
        siteId=site_id,
        jurisdiction=jurisdiction,
        windowHours=hours,
        overallStatus="exceedance" if any(r.get("status") == "exceedance" for r in results) else "compliant",
        escalations=escalations,
    )


@mcp.tool()
async def assess_sensor_validity(device_id: str, parameter: str, hours: int = 48) -> dict:
    """
    Decide whether a suspicious reading is a WATER problem or a SENSOR problem.

    Checks for the three classic analyzer faults:
      flatline   - zero variance (frozen output / failed cell)
      railed     - pinned at range limit (calibration or fouling)
      drift      - monotonic creep with no process explanation

    UC2 must never trigger a public health escalation on a dead analyzer, and
    must never dismiss a real event as 'probably the sensor'.
    """
    rows = within_hours(filter_rows(load_csv("water_quality"), limit=20000, deviceId=device_id), hours)
    values = [r[parameter] for r in rows if r.get(parameter) is not None]
    if len(values) < 6:
        return ok(None, message="Insufficient samples to assess sensor validity.")

    sd = statistics.pstdev(values)
    mean = statistics.fmean(values)
    flatline = sd < 1e-6
    railed = all(v == values[0] for v in values[-6:]) and (values[0] == 0 or values[0] == max(values))
    half = len(values) // 2
    drift = abs(statistics.fmean(values[half:]) - statistics.fmean(values[:half])) > 0.5 * (sd or 1)

    calibration = filter_rows(load_csv("calibrations"), limit=10, deviceId=device_id, parameter=parameter)
    last_cal = calibration[0] if calibration else None

    faults = [n for n, f in (("flatline", flatline), ("railed", railed), ("drift", drift)) if f]
    verdict = (
        "sensor fault likely - validate with a manual grab sample before acting"
        if faults
        else "sensor behaving normally - treat readings as real water quality"
    )
    return ok(
        {
            "deviceId": device_id,
            "parameter": parameter,
            "samples": len(values),
            "mean": round(mean, 4),
            "stdev": round(sd, 5),
            "suspectedFaults": faults,
            "lastCalibration": last_cal,
            "verdict": verdict,
        },
        windowHours=hours,
    )


@mcp.tool()
async def get_dma_flow(dma_id: str, hours: int = 168) -> dict:
    """
    District Metered Area inflow profile with minimum night flow (MNF).

    MNF between 02:00-04:00 is the canonical leak indicator: legitimate night
    consumption is small and stable, so a rising MNF floor is almost always a
    new or growing leak.
    """
    rows = within_hours(filter_rows(load_csv("dma_flow"), limit=20000, dmaId=dma_id), hours)
    if not rows:
        return ok(None, message=f"No flow data for DMA '{dma_id}'.")
    nights = [r for r in rows if 2 <= int(str(r.get("hourOfDay") or 0)) <= 4]
    mnf_values = [float(r["flowM3h"]) for r in nights if r.get("flowM3h") is not None]
    daily = [float(r["flowM3h"]) for r in rows if r.get("flowM3h") is not None]
    baseline = float(rows[0].get("baselineMnfM3h") or 0)
    current_mnf = round(statistics.fmean(mnf_values), 2) if mnf_values else None
    excess = round(current_mnf - baseline, 2) if current_mnf is not None and baseline else None
    return ok(
        {
            "dmaId": dma_id,
            "currentMnfM3h": current_mnf,
            "baselineMnfM3h": baseline,
            "excessMnfM3h": excess,
            "excessPercent": round(excess / baseline * 100, 1) if excess and baseline else None,
            "meanFlowM3h": round(statistics.fmean(daily), 2) if daily else None,
            "estimatedLossM3PerDay": round(excess * 24, 1) if excess and excess > 0 else 0.0,
            "verdict": (
                "probable leak - MNF materially above baseline"
                if excess and baseline and excess / baseline > 0.15
                else "MNF within normal variation"
            ),
        },
        windowHours=hours,
    )


@mcp.tool()
async def get_pressure_profile(dma_id: str, hours: int = 48) -> dict:
    """
    Pressure across DMA sensors - used to localise a burst.

    The sensor with the largest and earliest pressure drop is closest to the
    burst; ranking the drops narrows the search from a whole DMA to a street.
    """
    rows = within_hours(filter_rows(load_csv("dma_pressure"), limit=20000, dmaId=dma_id), hours)
    by_sensor: dict[str, list[float]] = {}
    for row in rows:
        if row.get("pressureBar") is not None:
            by_sensor.setdefault(row["sensorId"], []).append(float(row["pressureBar"]))
    profile = []
    for sensor, values in by_sensor.items():
        baseline = statistics.fmean(values[: max(len(values) // 3, 1)])
        recent = statistics.fmean(values[-max(len(values) // 6, 1):])
        profile.append(
            {
                "sensorId": sensor,
                "baselineBar": round(baseline, 3),
                "recentBar": round(recent, 3),
                "dropBar": round(baseline - recent, 3),
                "dropPercent": round((baseline - recent) / baseline * 100, 1) if baseline else 0.0,
            }
        )
    profile.sort(key=lambda p: p["dropBar"], reverse=True)
    return ok(
        profile,
        dmaId=dma_id,
        windowHours=hours,
        likelyNearestSensor=profile[0]["sensorId"] if profile else None,
    )


@mcp.tool()
async def calculate_nrw(dma_id: str, days: int = 30) -> dict:
    """Non-revenue water: input volume vs billed volume, with financial impact."""
    rows = filter_rows(load_csv("nrw_monthly"), limit=24, dmaId=dma_id)
    if not rows:
        return ok(None, message=f"No NRW record for '{dma_id}'.")
    latest = rows[-1]
    input_m3 = float(latest.get("inputM3") or 0)
    billed_m3 = float(latest.get("billedM3") or 0)
    nrw = input_m3 - billed_m3
    return ok(
        {
            "dmaId": dma_id,
            "periodDays": days,
            "inputM3": input_m3,
            "billedM3": billed_m3,
            "nrwM3": round(nrw, 1),
            "nrwPercent": round(nrw / input_m3 * 100, 1) if input_m3 else None,
            "costPerM3": latest.get("costPerM3"),
            "annualisedCost": round(nrw / days * 365 * float(latest.get("costPerM3") or 0), 0),
        }
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
