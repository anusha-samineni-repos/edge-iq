"""
Data-driven findings for local demo mode.

When no Foundry model is configured, each specialist still calls the *real* MCP
tool functions in-process (same code the MCP gateway serves) and renders the
results as a grounded answer. Demo answers therefore reflect the seeded data
rather than a canned card.
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable

_DEVICE_RE = re.compile(r"\b([A-Z]{2,4}-\d{1,3}-[A-Z0-9]{2,6}-\d{1,3})\b")
_SITE_RE = re.compile(r"\b((?:WTP|WWTP|PS|RES|DMA|WELL)-\d{1,3})\b")
_SHORT_DEVICE_RE = re.compile(r"\b([A-Z]{2,6}-\d{1,3})\b")


def _question(prompt: str) -> str:
    if "## OPERATOR QUESTION" in prompt:
        return prompt.split("## OPERATOR QUESTION", 1)[1].split("##", 1)[0].strip()
    return prompt.strip()


def _ids(question: str) -> tuple[list[str], list[str]]:
    upper = question.upper()
    devices = sorted(set(_DEVICE_RE.findall(upper)))
    sites = sorted(set(_SITE_RE.findall(upper)))
    # "PUMP-003 at WTP-01" -> WTP-01-PUMP-003
    if sites:
        for short in _SHORT_DEVICE_RE.findall(upper):
            if short not in sites and not short.startswith(("WTP", "WWTP", "PS-", "RES", "DMA", "WELL")):
                devices.append(f"{sites[0]}-{short}")
    for d in devices:
        parts = d.split("-")
        site = f"{parts[0]}-{parts[1]}"
        if site not in sites:
            sites.append(site)
    return sorted(set(devices)), sites


def _days_hint(question: str, default: int) -> int:
    m = re.search(r"(\d+)\s*days?", question.lower())
    return int(m.group(1)) if m else default


# --------------------------------------------------------------------------- UC1
async def _asset_health(q: str) -> str:
    from mcp_servers.servers import asset_registry as ar, historian as h

    devices, sites = _ids(q)
    candidates = [d for d in devices if any(k in d for k in ("PUMP", "BLOW", "MOT"))] or devices
    if not candidates:
        return "No specific asset was named. Ask about an asset ID such as `WTP-01-PUMP-003`."
    out = []
    for dev in candidates[:3]:
        asset = (await ar.get_asset(dev)).get("data")
        if not asset:
            out.append(f"- `{dev}`: not found in the asset registry.")
            continue
        limits = (await ar.get_vibration_limits(dev)).get("data") or {}
        stats = (await h.get_statistics(dev, "vibration_mm_s", 168)).get("data")
        alarms = (await h.get_alarms(device_id=dev)).get("data") or []
        zones = limits.get("zones", {})
        zone = "?"
        if stats and zones:
            p95 = stats["p95"]
            zone = "A" if p95 < zones["A_B"] else "B" if p95 < zones["B_C"] else "C" if p95 < zones["C_D"] else "D"
        verdict = {"D": "**Not healthy - act now**", "C": "**Unsatisfactory - plan intervention**"}.get(zone, "Healthy")
        lines = [
            f"#### {dev} - {verdict}",
            f"- Asset: {asset.get('manufacturer')} {asset.get('model')}, {asset.get('ratedPowerKw')} kW, "
            f"criticality **{asset.get('criticality')}**, age {asset.get('ageYears')} y.",
        ]
        if stats:
            lines.append(
                f"- Vibration (7 d): mean {stats['mean']} mm/s, p95 **{stats['p95']} mm/s**, max {stats['max']} mm/s, "
                f"trend **{stats['trend']}** ({stats['changePercent']:+}%)."
            )
        if zones:
            lines.append(
                f"- ISO 10816-3 band {zones.get('note')}: B/C {zones['B_C']}, C/D {zones['C_D']} mm/s -> p95 sits in **zone {zone}**."
            )
        for a in alarms[:3]:
            lines.append(f"- Active alarm `{a['alarmId']}` ({a['severity']}): {a['title']} - {a['detail']}.")
        if zone in ("C", "D"):
            lines.append(
                "- **Recommended action:** inspect bearings/alignment, confirm with a route-based vibration "
                "reading, and raise a P1/P2 work order (approval required)."
            )
        out.append("\n".join(lines))
    return "\n\n".join(out)


# --------------------------------------------------------------------------- UC2
async def _water_quality(q: str) -> str:
    from mcp_servers.servers import edge_fleet as ef, water_quality as wq

    devices, sites = _ids(q)
    sites = [s for s in sites if not s.startswith("DMA")] or ["RES-03"]
    out = []
    for site in sites[:2]:
        comp = await wq.check_compliance(site, 168)
        rows = comp.get("data") or []
        lines = [f"#### Compliance at {site} (last 7 days)"]
        for r in rows:
            flag = "**EXCEEDANCE**" if r["status"] != "compliant" else "compliant"
            lines.append(
                f"- {r['parameter']}: min {r['min']}, max {r['max']} {r['unit']} "
                f"(limits {r['limitMin']}-{r['limitMax']}) -> {flag}, {r['breachCount']} breaches, tier {r['tier']}. "
                f"_{r['citation']}_"
            )
        # Sensor sanity check before declaring a real breach.
        devs = (await ef.list_devices(site_id=site)).get("data") or []
        for d in devs:
            if any(k in d["deviceId"] for k in ("CL2", "WQ")):
                v = (await wq.assess_sensor_validity(d["deviceId"], "chlorine_mg_l")).get("data")
                if v:
                    faults = v.get("suspectedFaults") or []
                    lines.append(
                        f"- Sensor check `{d['deviceId']}`: stdev {v['stdev']}, suspected faults: "
                        f"**{', '.join(faults) or 'none'}**."
                        + (" Treat readings as untrustworthy - dispatch a grab sample." if "flatline" in faults else "")
                    )
        if any(r["status"] != "compliant" for r in rows):
            lines.append(
                "- **Recommended action:** take a confirmatory grab sample, increase dosing per the chlorine "
                "residual SOP, and notify the compliance officer within the reporting window."
            )
        out.append("\n".join(lines))
    return "\n\n".join(out)


# --------------------------------------------------------------------------- UC3
async def _leak(q: str) -> str:
    from mcp_servers.servers import water_quality as wq

    _, sites = _ids(q)
    dmas = [s for s in sites if s.startswith("DMA")] or ["DMA-14"]
    out = []
    for dma in dmas[:2]:
        flow = (await wq.get_dma_flow(dma)).get("data")
        nrw = (await wq.calculate_nrw(dma)).get("data")
        lines = [f"#### {dma}"]
        if flow:
            lines.append(
                f"- Minimum night flow **{flow['currentMnfM3h']} m³/h** vs baseline {flow['baselineMnfM3h']} m³/h "
                f"(+{flow['excessPercent']}%) -> estimated loss **{flow['estimatedLossM3PerDay']} m³/day**. "
                f"Verdict: **{flow['verdict']}**."
            )
        if nrw:
            lines.append(
                f"- NRW (30 d): **{nrw['nrwPercent']}%** ({nrw['nrwM3']:,.0f} m³), annualised cost "
                f"**${nrw['annualisedCost']:,.0f}**."
            )
        lines.append("- **Recommended action:** step-test the DMA, deploy acoustic loggers, then raise a repair work order.")
        out.append("\n".join(lines))
    return "\n\n".join(out)


# --------------------------------------------------------------------------- UC4
async def _energy(q: str) -> str:
    from mcp_servers.servers import historian as h

    devices, _ = _ids(q)
    pumps = [d for d in devices if "PUMP" in d] or ["PS-04-PUMP-001", "WTP-01-PUMP-003"]
    lines = ["#### Pump energy (last 7 days)"]
    for dev in pumps[:4]:
        s = (await h.get_statistics(dev, "power_kw", 168)).get("data")
        if s:
            kwh = s["mean"] * 24 * 7
            lines.append(
                f"- `{dev}`: mean {s['mean']} kW (p95 {s['p95']} kW), ~{kwh:,.0f} kWh/week, trend {s['trend']}."
            )
    lines.append(
        "- **Opportunity:** shift reservoir filling to off-peak tariff windows while keeping levels within "
        "operating bands; review pumps whose kW is rising at constant flow (efficiency loss)."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- UC5
async def _fleet(q: str) -> str:
    from mcp_servers.servers import edge_fleet as ef

    lowered = q.lower()
    devices, sites = _ids(q)
    lines: list[str] = []
    if "cert" in lowered:
        days = _days_hint(q, 30)
        c = await ef.get_certificate_status(sites[0] if sites else None, days)
        rows = c.get("data") or []
        lines.append(f"#### Certificates expiring within {days} days: {len(rows)}")
        for r in rows:
            lines.append(f"- `{r['deviceId']}` ({r['siteId']}): **{r['certDaysToExpiry']} days** left, status {r['status']}.")
        if rows:
            lines.append("- **Action:** rotate via DPS enrollment-group renewal before expiry (see certificate SOP).")
    for dev in devices:
        d = (await ef.get_device(dev)).get("data")
        if not d:
            continue
        lines.append(
            f"#### {dev}\n- Status **{d['status']}**, last message {d['minutesSinceLastMessage']} min ago, "
            f"{d['connectivity']} link, runtime {d['edgeRuntimeVersion']}, cert {d['certDaysToExpiry']} d."
        )
        if d["deviceType"] == "gateway" or "GW" in dev:
            twin = (await ef.get_module_twin(dev)).get("data") or []
            drift = [t for t in twin if t["desiredValue"] != t["reportedValue"]]
            for t in drift:
                lines.append(
                    f"- Twin drift `{t['moduleId']}.{t['property']}`: desired {t['desiredValue']}, reported **{t['reportedValue']}**."
                )
    if any(w in lowered for w in ("flat", "stuck", "frozen", "drift", "calibrat")) and sites:
        from mcp_servers.servers import water_quality as wq

        for site in sites:
            for d in (await ef.list_devices(site_id=site)).get("data") or []:
                if any(k in d["deviceId"] for k in ("CL2", "WQ")):
                    v = (await wq.assess_sensor_validity(d["deviceId"], "chlorine_mg_l")).get("data")
                    if v:
                        faults = v.get("suspectedFaults") or []
                        lines.append(
                            f"#### Sensor validity `{d['deviceId']}`\n- {v['samples']} samples, mean {v['mean']}, "
                            f"stdev **{v['stdev']}** -> suspected faults: **{', '.join(faults) or 'none'}**. "
                            f"Device status {d['status']}, last message {d['minutesSinceLastMessage']} min ago."
                        )
                        if "flatline" in faults:
                            lines.append("- **Verdict:** sensor fault likely (not a real water-quality change). "
                                         "Recalibrate/replace the analyser and take a grab sample meanwhile.")
    if any(w in lowered for w in ("offline", "not reporting", "stopped", "no data", "missing", "dark")) and not devices:
        off = (await ef.list_devices(status="offline")).get("data") or []
        lines.append(f"#### Offline devices: {len(off)}")
        for d in off:
            lines.append(f"- `{d['deviceId']}` ({d['siteId']}): {d['minutesSinceLastMessage']} min silent, {d['connectivity']}.")
    if "drift" in lowered and not devices:
        twin = (await ef.get_module_twin("WTP-01-GW-001")).get("data") or []
        for t in twin:
            if t["desiredValue"] != t["reportedValue"]:
                lines.append(f"- `WTP-01-GW-001` {t['moduleId']}.{t['property']}: desired {t['desiredValue']}, reported **{t['reportedValue']}**.")
    if not lines or any(w in lowered for w in ("fleet", "briefing", "estate", "overview", "status")):
        s = (await ef.get_fleet_summary()).get("data") or {}
        by = s.get("byStatus", {})
        lines.insert(
            0,
            f"#### Fleet summary\n- {s.get('totalDevices')} devices: {by.get('online', 0)} online, "
            f"{by.get('degraded', 0)} degraded, {by.get('offline', 0)} offline. Health score **{s.get('healthScore')}**.\n"
            f"- Stale telemetry: {', '.join(s.get('staleTelemetry', [])) or 'none'}; "
            f"certificate risk: {', '.join(s.get('certificateRisk', [])) or 'none'}.",
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- planner / KB
async def _maintenance(q: str) -> str:
    from mcp_servers.servers import asset_registry as ar, work_order as wo

    devices, sites = _ids(q)
    asset_id = next((d for d in devices if "PUMP" in d or "BLOW" in d), devices[0] if devices else "WTP-01-PUMP-003")
    site_id = "-".join(asset_id.split("-")[:2])
    asset = (await ar.get_asset(asset_id)).get("data") or {}
    pri = (await wo.calculate_priority(asset.get("criticality", "high"), "availability")).get("data") or {}
    proposal = await wo.create_work_order(
        asset_id=asset_id,
        site_id=site_id,
        title=f"Investigate high vibration - {asset_id}",
        description="Vibration trending into ISO zone C/D; inspect bearings and alignment.",
        priority=pri.get("priority", "P2"),
        failure_mode="Bearing wear",
        estimated_hours=6,
    )
    p = proposal["data"]
    return (
        "#### Proposed action - approval required\n"
        f"- Work order **{p['workOrderId']}** for `{p['assetId']}` at {p['siteId']}\n"
        f"- Title: {p['title']}\n- Priority **{p['priority']}** (target response {pri.get('targetResponseHours')} h, "
        f"criticality {asset.get('criticality')})\n- Failure mode: {p['failureMode']}, est. {p['estimatedHours']} h\n\n"
        f"_{proposal['message']}_ Approve in the panel to submit."
    )


async def _knowledge(prompt: str) -> str:
    block = prompt.split("### Foundry IQ knowledge", 1)
    if len(block) < 2:
        return "No matching SOPs or manuals were found in the knowledge base for this question."
    body = block[1].split("\n### ", 1)[0].strip()
    return "#### From the knowledge base\n" + body[:1800]


_HANDLERS: dict[str, Callable[[str], Awaitable[str]]] = {
    "asset-health": _asset_health,
    "water-quality": _water_quality,
    "leak-detection": _leak,
    "energy-optimizer": _energy,
    "fleet-operations": _fleet,
    "maintenance-planner": _maintenance,
}


async def compose(agent_name: str, prompt: str) -> str | None:
    if agent_name == "knowledge-navigator":
        return await _knowledge(prompt)
    handler = _HANDLERS.get(agent_name)
    if handler is None:
        return None
    return await handler(_question(prompt))
