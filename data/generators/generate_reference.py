#!/usr/bin/env python3
"""
Generates the Edge IQ reference tables: regulatory limits, failure modes (FMEA),
spare parts, crews, alarms, work orders, module twins, connectivity events,
deployments, calibrations and NRW.

These are the *slowly changing* tables. They are generated rather than
hand-written so that asset IDs always match ``assets.csv``, but the content
(limits, failure modes, priority policy) is real domain data a utility would
recognise and can edit directly in the CSV.

    python data/generators/generate_reference.py
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {path.relative_to(ROOT)}  ({len(rows)} rows)")


REGULATORY_LIMITS = [
    # US EPA Safe Drinking Water Act
    dict(jurisdiction="US-EPA", parameter="chlorine_mg_l", unit="mg/L", minValue=0.2, maxValue=4.0,
         limitType="MRDL", tier=2, notificationHours=720,
         requiredAction="Investigate residual loss; flush main; resample within 24h.",
         citation="40 CFR 141.65 - MRDL 4.0 mg/L; state minimum detectable residual 0.2 mg/L"),
    dict(jurisdiction="US-EPA", parameter="turbidity_ntu", unit="NTU", minValue="", maxValue=1.0,
         limitType="MCL", tier=1, notificationHours=24,
         requiredAction="Tier 1 public notification within 24 hours; consult primacy agency.",
         citation="40 CFR 141.71 - combined filter effluent must not exceed 1 NTU"),
    dict(jurisdiction="US-EPA", parameter="ph", unit="pH", minValue=6.5, maxValue=8.5,
         limitType="SMCL", tier=3, notificationHours=8760,
         requiredAction="Adjust corrosion control; record in annual CCR.",
         citation="40 CFR 143.3 - secondary standard"),
    dict(jurisdiction="US-EPA", parameter="temperature_c", unit="degC", minValue="", maxValue=25.0,
         limitType="operational", tier=3, notificationHours=8760,
         requiredAction="Monitor for accelerated residual decay and nitrification.",
         citation="Operational guidance - not a federal MCL"),
    dict(jurisdiction="US-EPA", parameter="conductivity_us_cm", unit="uS/cm", minValue="", maxValue=1500,
         limitType="operational", tier=3, notificationHours=8760,
         requiredAction="Investigate source change or intrusion.",
         citation="Operational surrogate for TDS SMCL 500 mg/L"),
    # UK DWI
    dict(jurisdiction="UK-DWI", parameter="chlorine_mg_l", unit="mg/L", minValue=0.2, maxValue=2.0,
         limitType="operational", tier=2, notificationHours=720,
         requiredAction="Report to DWI if residual loss is systemic.",
         citation="Water Supply (Water Quality) Regulations 2016"),
    dict(jurisdiction="UK-DWI", parameter="turbidity_ntu", unit="NTU", minValue="", maxValue=1.0,
         limitType="PCV", tier=1, notificationHours=24,
         requiredAction="Notify DWI; consider precautionary boil notice.",
         citation="WSWQ Regulations 2016 Sch.1 Part 3"),
    dict(jurisdiction="UK-DWI", parameter="ph", unit="pH", minValue=6.5, maxValue=9.5,
         limitType="PCV", tier=3, notificationHours=8760,
         requiredAction="Adjust treatment.", citation="WSWQ Regulations 2016 Sch.1 Part 3"),
    # WHO
    dict(jurisdiction="WHO", parameter="chlorine_mg_l", unit="mg/L", minValue=0.2, maxValue=5.0,
         limitType="guideline", tier=2, notificationHours=720,
         requiredAction="Maintain >=0.2 mg/L at point of delivery.",
         citation="WHO Guidelines for Drinking-water Quality, 4th ed."),
    dict(jurisdiction="WHO", parameter="turbidity_ntu", unit="NTU", minValue="", maxValue=1.0,
         limitType="guideline", tier=1, notificationHours=24,
         requiredAction="Investigate immediately; disinfection may be compromised.",
         citation="WHO GDWQ 4th ed. - <1 NTU for effective disinfection"),
]

FAILURE_MODES = [
    dict(assetClass="pump", failureMode="Bearing wear", signature="Rising overall vibration with 2x/3x BPFO sidebands; bearing temp rise 10-20 degC",
         detectableVia="vibration_mm_s, bearing_temp_c", typicalLeadTimeDays=21,
         consequence="availability", recommendedAction="Schedule bearing replacement; collect a spectrum before shutdown."),
    dict(assetClass="pump", failureMode="Impeller imbalance", signature="Dominant 1x running speed vibration, radial direction, stable with load",
         detectableVia="vibration_mm_s", typicalLeadTimeDays=45,
         consequence="availability", recommendedAction="Inspect impeller for debris/erosion; balance on reassembly."),
    dict(assetClass="pump", failureMode="Coupling misalignment", signature="High 2x running speed, axial vibration >50% of radial",
         detectableVia="vibration_mm_s", typicalLeadTimeDays=60,
         consequence="availability", recommendedAction="Laser-align at operating temperature."),
    dict(assetClass="pump", failureMode="Cavitation", signature="Broadband high-frequency noise; suction pressure below NPSHr; erratic flow",
         detectableVia="suction_pressure_bar, flow_m3h, vibration_mm_s", typicalLeadTimeDays=14,
         consequence="availability", recommendedAction="Raise suction head or reduce speed; inspect impeller for pitting."),
    dict(assetClass="pump", failureMode="Seal failure", signature="Motor current rise with no flow increase; gland leakage",
         detectableVia="motor_current_a, flow_m3h", typicalLeadTimeDays=10,
         consequence="availability", recommendedAction="Replace mechanical seal; check flush line."),
    dict(assetClass="pump", failureMode="Wear-ring clearance loss", signature="Flow down and power up at constant head - efficiency drift off BEP",
         detectableVia="flow_m3h, power_kw, discharge_pressure_bar", typicalLeadTimeDays=120,
         consequence="efficiency", recommendedAction="Measure clearances at next overhaul; quantify kWh/ML penalty."),
    dict(assetClass="blower", failureMode="Filter blockage", signature="Discharge temp rise with falling airflow at constant power",
         detectableVia="power_kw, flow_m3h", typicalLeadTimeDays=7,
         consequence="efficiency", recommendedAction="Replace inlet filter element."),
    dict(assetClass="analyzer", failureMode="Reagent depletion", signature="Slow downward drift then flatline; often exactly 30 days after service",
         detectableVia="chlorine_mg_l", typicalLeadTimeDays=3,
         consequence="compliance", recommendedAction="Replace reagent; verify with DPD grab sample."),
    dict(assetClass="analyzer", failureMode="Fouled measuring cell", signature="Suppressed reading with reduced variance; recovers after cleaning",
         detectableVia="chlorine_mg_l, turbidity_ntu", typicalLeadTimeDays=5,
         consequence="compliance", recommendedAction="Clean cell; recalibrate against grab sample."),
    dict(assetClass="analyzer", failureMode="Frozen output", signature="Zero variance across many samples - value identical to 3+ decimals",
         detectableVia="chlorine_mg_l", typicalLeadTimeDays=0,
         consequence="compliance", recommendedAction="Do NOT trust the reading. Grab sample now; power-cycle and recalibrate."),
    dict(assetClass="flowmeter", failureMode="Electrode coating", signature="Progressive under-reading vs mass balance; noisy at low flow",
         detectableVia="flow_m3h", typicalLeadTimeDays=90,
         consequence="efficiency", recommendedAction="Clean electrodes; verify against portable clamp-on meter."),
    dict(assetClass="prv", failureMode="Diaphragm fatigue", signature="Downstream pressure hunting; outlet pressure creeps at low demand",
         detectableVia="pressureBar", typicalLeadTimeDays=30,
         consequence="availability", recommendedAction="Rebuild PRV; re-set outlet to DMA target."),
    dict(assetClass="gateway", failureMode="Certificate expiry", signature="Abrupt, total loss of telemetry at an exact timestamp; TLS handshake errors",
         detectableVia="certDaysToExpiry, minutesSinceLastMessage", typicalLeadTimeDays=0,
         consequence="availability", recommendedAction="Rotate the X.509 certificate before expiry - fully preventable."),
    dict(assetClass="gateway", failureMode="Cellular backhaul instability", signature="Repeated short disconnects; store-and-forward backfill bursts",
         detectableVia="connectivity_events", typicalLeadTimeDays=14,
         consequence="availability", recommendedAction="Check signal strength and antenna; consider failover APN."),
    dict(assetClass="gateway", failureMode="Module config drift", signature="Reported twin properties differ from desired after a deployment",
         detectableVia="module_twins", typicalLeadTimeDays=0,
         consequence="availability", recommendedAction="Re-apply the deployment manifest; confirm reported == desired."),
    dict(assetClass="valve", failureMode="Actuator stiction", signature="Position error grows; travel time increases run over run",
         detectableVia="valve_position", typicalLeadTimeDays=60,
         consequence="availability", recommendedAction="Lubricate/overhaul actuator; verify stroke."),
]

SPARE_PART_TEMPLATES = {
    "pump": [("BRG-6314-C3", "Bearing, deep groove 6314 C3", 340.0, 4, 2),
             ("SEAL-MECH-75", "Mechanical seal cartridge 75mm", 1250.0, 2, 1),
             ("IMP-NK150", "Impeller, NK 150-400", 6800.0, 1, 0),
             ("COUP-EL-180", "Coupling element 180", 210.0, 4, 2)],
    "blower": [("FLT-D62S", "Inlet filter element D62S", 180.0, 6, 2),
               ("BELT-SPB-2650", "Drive belt SPB 2650", 95.0, 4, 2)],
    "analyzer": [("RGT-CL17-A", "CL17sc reagent set A", 145.0, 8, 4),
                 ("CELL-CL17", "Colorimetric measuring cell", 620.0, 2, 1),
                 ("RGT-TU5300", "TU5300sc calibration kit", 310.0, 3, 1)],
    "flowmeter": [("ELEC-PW400", "Electrode pair, Promag W 400", 480.0, 2, 1)],
    "prv": [("DIA-9001", "Diaphragm kit, Cla-Val 90-01", 390.0, 3, 1)],
    "valve": [("ACT-B700", "Actuator service kit 700 series", 275.0, 2, 1)],
    "gateway": [("CERT-X509", "X.509 device certificate (issued)", 0.0, 999, 0),
                ("ANT-LTE-5DB", "LTE antenna 5dBi", 65.0, 5, 2)],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "customdata")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    out = args.out
    now = datetime.now(timezone.utc)

    assets = read_csv(out / "assets.csv")
    devices = read_csv(out / "devices.csv")
    if not assets:
        raise SystemExit("Run generate_telemetry.py first - assets.csv is missing.")

    print("Generating Edge IQ reference tables ->", out)

    write_csv(out / "regulatory_limits.csv", REGULATORY_LIMITS)
    write_csv(out / "failure_modes.csv", FAILURE_MODES)

    # ----------------------------------------------------------- spare parts
    parts = []
    for asset in assets:
        for pn, desc, cost, on_hand, min_stock in SPARE_PART_TEMPLATES.get(asset["assetClass"], []):
            parts.append(
                dict(assetId=asset["assetId"], partNumber=pn, description=desc,
                     unitCost=cost, onHand=max(0, on_hand - rng.randint(0, 3)),
                     minStock=min_stock, leadTimeDays=rng.choice([3, 7, 14, 28]),
                     supplier=rng.choice(["Xylem Parts", "Grainger", "OEM Direct", "RS Components"]))
            )
    write_csv(out / "spare_parts.csv", parts)

    # ---------------------------------------------------------------- crews
    crews = []
    skills = ["mechanical", "electrical", "instrumentation", "water quality", "network"]
    for site in {a["siteId"] for a in assets}:
        for day in range(21):
            date = (now + timedelta(days=day)).date().isoformat()
            for skill in skills:
                crews.append(
                    dict(siteId=site, date=date, skill=skill,
                         crewId=f"CREW-{skill[:3].upper()}-{rng.randint(1, 4)}",
                         availableHours=rng.choice([0, 0, 4, 8, 8, 12]),
                         shift=rng.choice(["day", "day", "night"]))
                )
    write_csv(out / "crew_availability.csv", crews)

    # --------------------------------------------------------------- alarms
    alarms = []
    alarm_specs = [
        ("WTP-01-PUMP-003", "critical", "Vibration high-high", "vibration_mm_s exceeded 5.6 mm/s (ISO zone D)", 6, "active"),
        ("WTP-01-PUMP-003", "high", "Bearing temperature high", "bearing_temp_c exceeded 75 degC", 18, "active"),
        ("RES-03-CL2-001", "high", "Chlorine residual low", "chlorine_mg_l below 0.20 mg/L action level", 9, "active"),
        ("RES-07-CL2-001", "medium", "Analyzer data quality", "Zero variance detected over 72 samples", 30, "active"),
        ("DMA-14-FLOW-001", "high", "Minimum night flow elevated", "MNF 19.2 m3/h vs baseline 11.5 m3/h", 12, "active"),
        ("WELL-09-GW-001", "critical", "Device offline", "No telemetry for 72 hours", 72, "active"),
        ("WTP-01-GW-001", "high", "Certificate expiring", "Device certificate expires in 11 days", 48, "active"),
        ("PS-04-PUMP-001", "medium", "Specific energy high", "kWh/ML 14% above benchmark", 24, "active"),
        ("WTP-02-PUMP-001", "low", "Runtime hours milestone", "5000 hour service interval reached", 96, "acknowledged"),
        ("WWTP-01-BLOW-001", "medium", "Inlet filter differential", "Filter dP above 25 mbar", 40, "cleared"),
    ]
    for idx, (device, severity, title, detail, hours_ago, state) in enumerate(alarm_specs, start=1):
        site = "-".join(device.split("-")[:2])
        alarms.append(
            dict(alarmId=f"ALM-{idx:05d}", deviceId=device, siteId=site, severity=severity,
                 title=title, detail=detail, state=state,
                 raisedAt=(now - timedelta(hours=hours_ago)).isoformat(),
                 clearedAt=(now - timedelta(hours=hours_ago - 6)).isoformat() if state == "cleared" else "",
                 source="edge-rule-engine")
        )
    write_csv(out / "alarms.csv", alarms)

    # ---------------------------------------------------------- work orders
    workorders = []
    pumps = [a for a in assets if a["assetClass"] in ("pump", "blower")]
    for idx, asset in enumerate(pumps * 2, start=1):
        completed = now - timedelta(days=rng.randint(30, 900))
        mode = rng.choice([f["failureMode"] for f in FAILURE_MODES if f["assetClass"] == asset["assetClass"]] or ["Routine service"])
        workorders.append(
            dict(workOrderId=f"WO-{idx:05d}", assetId=asset["assetId"], siteId=asset["siteId"],
                 title=f"{mode} - {asset['assetId']}", description=f"Corrective maintenance for {mode.lower()}.",
                 priority=rng.choice(["P2", "P3", "P3", "P4"]), status="closed",
                 failureMode=mode, createdAt=(completed - timedelta(days=rng.randint(1, 14))).isoformat(),
                 completedAt=completed.isoformat(), downtimeHours=round(rng.uniform(1.5, 26.0), 1),
                 labourHours=round(rng.uniform(2, 18), 1), crewId=f"CREW-MEC-{rng.randint(1,4)}",
                 costUsd=round(rng.uniform(400, 9500), 2))
        )
    workorders.append(
        dict(workOrderId="WO-09001", assetId="WTP-01-PUMP-003", siteId="WTP-01",
             title="Investigate rising vibration - WTP-01-PUMP-003",
             description="Vibration trending toward ISO zone D. Inspect drive-end bearing.",
             priority="P2", status="open", failureMode="Bearing wear",
             createdAt=(now - timedelta(days=2)).isoformat(), completedAt="",
             downtimeHours=0, labourHours=0, crewId="", costUsd=0)
    )
    write_csv(out / "workorders.csv", workorders)

    # -------------------------------------------------------- module twins
    twins = []
    for device in devices:
        if device["deviceType"] != "gateway":
            continue
        drift = device["deviceId"] == "WTP-01-GW-001"
        for module, prop, desired in [
            ("telemetryModule", "samplingIntervalSeconds", "60"),
            ("telemetryModule", "batchSize", "100"),
            ("edgeAgent", "upstreamProtocol", "Amqp"),
            ("filterModule", "vibrationThresholdMmS", "4.5"),
            ("storeForwardModule", "retentionHours", "48"),
        ]:
            reported = desired
            if drift and prop == "samplingIntervalSeconds":
                reported = "300"      # drifted - 5x slower sampling
            if drift and prop == "vibrationThresholdMmS":
                reported = "9.0"      # drifted - alarm threshold raised
            twins.append(dict(deviceId=device["deviceId"], moduleId=module, property=prop,
                              desiredValue=desired, reportedValue=reported,
                              lastUpdated=(now - timedelta(hours=rng.randint(1, 200))).isoformat()))
    write_csv(out / "module_twins.csv", twins)

    # -------------------------------------------------- connectivity events
    events = []
    for device in devices:
        count = 14 if device["deviceId"] == "WTP-01-GW-001" else (1 if device["status"] == "offline" else rng.randint(0, 2))
        for i in range(count):
            ts = now - timedelta(hours=rng.randint(1, 72))
            duration = rng.randint(2, 25) if device["deviceId"] == "WTP-01-GW-001" else rng.randint(1, 8)
            if device["status"] == "offline":
                ts, duration = now - timedelta(hours=72), 4320
            events.append(dict(deviceId=device["deviceId"], siteId=device["siteId"],
                               event="disconnected", timestamp=ts.isoformat(),
                               durationMinutes=duration,
                               reason=rng.choice(["signal_loss", "tls_error", "power_cycle", "wan_timeout"])))
    write_csv(out / "connectivity_events.csv", events)

    # ----------------------------------------------------------- deployments
    deployments = [
        dict(deploymentId="DEP-2024-11-TELEMETRY", description="Telemetry module 2.3.0 rollout",
             targetCondition="tags.environment='production'", targetedCount=38, appliedCount=38,
             reportedSuccessCount=36, reportedFailureCount=2, priority=10,
             createdAt=(now - timedelta(days=6)).isoformat(), status="partially_succeeded"),
        dict(deploymentId="DEP-2024-10-SECURITY", description="TLS 1.3 enforcement + cert rotation",
             targetCondition="tags.deviceType='gateway'", targetedCount=10, appliedCount=10,
             reportedSuccessCount=9, reportedFailureCount=1, priority=20,
             createdAt=(now - timedelta(days=24)).isoformat(), status="partially_succeeded"),
    ]
    write_csv(out / "deployments.csv", deployments)

    # ---------------------------------------------------------- calibrations
    calibrations = []
    for asset in assets:
        if asset["assetClass"] != "analyzer":
            continue
        for parameter in ("chlorine_mg_l", "turbidity_ntu", "ph"):
            days_ago = 2 if asset["deviceId"] == "RES-07-CL2-001" else rng.randint(5, 120)
            calibrations.append(dict(deviceId=asset["deviceId"], parameter=parameter,
                                     calibratedAt=(now - timedelta(days=days_ago)).isoformat(),
                                     technician=rng.choice(["J. Okafor", "M. Lindqvist", "R. Patel", "S. Nguyen"]),
                                     preCalError=round(rng.uniform(-0.08, 0.08), 3),
                                     postCalError=round(rng.uniform(-0.01, 0.01), 3),
                                     nextDueDays=max(0, 90 - days_ago), result="pass"))
    write_csv(out / "calibrations.csv", calibrations)

    # ------------------------------------------------------------------ NRW
    nrw = []
    for dma, input_m3, billed in (("DMA-14", 412_000, 318_000), ("DMA-22", 286_000, 254_000)):
        for month in range(6, 0, -1):
            leak_factor = 1.0 + (0.06 if dma == "DMA-14" and month <= 2 else 0.0)
            nrw.append(dict(dmaId=dma, periodStart=(now - timedelta(days=30 * month)).date().isoformat(),
                            inputM3=round(input_m3 * leak_factor), billedM3=billed,
                            costPerM3=0.62, currency="USD"))
    write_csv(out / "nrw_monthly.csv", nrw)

    print("\nReference tables complete.")


if __name__ == "__main__":
    main()
