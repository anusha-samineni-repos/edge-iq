#!/usr/bin/env python3
"""
Edge IQ demo data generator.

Produces a coherent, realistic water-utility edge dataset in ``data/customdata``
that every one of the five use cases can be demonstrated against, with seeded
faults so the agents have something real to find.

The dataset is deliberately *small* (a few MB) so it can live in git and power a
zero-Azure demo, but its schema is identical to the Fabric Lakehouse tables, so
the same agent code runs unchanged against production.

Usage
-----
    python data/generators/generate_telemetry.py
    python data/generators/generate_telemetry.py --days 30 --seed 7
    python data/generators/generate_telemetry.py --out data/customdata

Tunable scenario parameters live in SCENARIO below - change a threshold or a
fault day and the demo narrative changes with it, no code edits needed.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------
# Scenario parameters - the "easy to change" knobs for a demo
# --------------------------------------------------------------------------
SCENARIO = {
    # UC1: bearing degradation on a raw water pump
    "uc1_device": "WTP-01-PUMP-003",
    "uc1_fault_start_day": 12,     # days ago the vibration trend begins
    "uc1_peak_vibration": 7.9,     # mm/s RMS reached today (ISO 10816-3 zone D for a 75 kW machine)

    # UC2: chlorine residual decay at a remote reservoir + a failing analyzer
    "uc2_device": "RES-03-CL2-001",
    "uc2_min_chlorine": 0.14,      # mg/L - below the 0.2 action level
    "uc2_flatline_device": "RES-07-CL2-001",
    "uc2_flatline_day": 3,

    # UC3: a developing leak in a district metered area
    "uc3_dma": "DMA-14",
    "uc3_leak_start_day": 9,
    "uc3_mnf_baseline": 11.5,      # m3/h
    "uc3_mnf_peak": 19.2,          # m3/h today

    # UC4: pump running off its best efficiency point + expensive tariff hours
    "uc4_device": "PS-04-PUMP-001",
    "uc4_efficiency_loss_pct": 14.0,

    # UC5: an edge gateway that drops out, and certificates about to expire
    "uc5_flaky_gateway": "WTP-01-GW-001",
    "uc5_offline_device": "WELL-09-GW-001",
    "uc5_cert_expiry_days": 11,
}

SITES = [
    ("WTP-01", "Riverside Water Treatment Plant", "treatment", 42.1, "Northern"),
    ("WTP-02", "Highland Water Treatment Plant", "treatment", 28.0, "Northern"),
    ("WWTP-01", "Eastbank Wastewater Plant", "wastewater", 36.5, "Eastern"),
    ("PS-04", "Kingsway Pumping Station", "pumping", 0.0, "Central"),
    ("PS-07", "Mill Road Pumping Station", "pumping", 0.0, "Southern"),
    ("RES-03", "Ashford Service Reservoir", "storage", 0.0, "Central"),
    ("RES-07", "Beacon Hill Reservoir", "storage", 0.0, "Southern"),
    ("DMA-14", "Oldtown District Metered Area", "distribution", 0.0, "Central"),
    ("DMA-22", "Harbourside District Metered Area", "distribution", 0.0, "Eastern"),
    ("WELL-09", "Cedar Grove Wellfield", "source", 8.2, "Western"),
]

ASSET_TEMPLATES = [
    # (suffix, class, rated kW, criticality, duty, manufacturer, model)
    ("PUMP-001", "pump", 110.0, "critical", "duty", "Grundfos", "NK 150-400"),
    ("PUMP-002", "pump", 110.0, "critical", "standby", "Grundfos", "NK 150-400"),
    ("PUMP-003", "pump", 75.0, "critical", "duty", "KSB", "Omega 200-500"),
    ("BLOW-001", "blower", 45.0, "high", "duty", "Aerzen", "Delta Hybrid D62S"),
    ("VALVE-001", "valve", 0.4, "medium", "modulating", "Bermad", "700 Series"),
    ("CL2-001", "analyzer", 0.1, "critical", "continuous", "Hach", "CL17sc"),
    ("TURB-001", "analyzer", 0.1, "high", "continuous", "Hach", "TU5300sc"),
    ("FLOW-001", "flowmeter", 0.1, "high", "continuous", "Endress+Hauser", "Promag W 400"),
    ("PRV-002", "prv", 0.0, "high", "pressure-reducing", "Cla-Val", "90-01"),
    ("GW-001", "gateway", 0.05, "critical", "edge", "Advantech", "UNO-2484G"),
]


def _rows_for_site(site_id: str, site_type: str) -> list[tuple]:
    """Not every site has every asset - keep the estate realistic."""
    if site_type == "treatment":
        keep = {"PUMP-001", "PUMP-002", "PUMP-003", "CL2-001", "TURB-001", "FLOW-001", "GW-001"}
    elif site_type == "wastewater":
        keep = {"PUMP-001", "BLOW-001", "FLOW-001", "GW-001"}
    elif site_type == "pumping":
        keep = {"PUMP-001", "PUMP-002", "FLOW-001", "VALVE-001", "GW-001"}
    elif site_type == "storage":
        keep = {"CL2-001", "TURB-001", "FLOW-001", "GW-001"}
    elif site_type == "distribution":
        keep = {"FLOW-001", "PRV-002", "GW-001"}
    else:  # source
        keep = {"PUMP-001", "FLOW-001", "GW-001"}
    return [t for t in ASSET_TEMPLATES if t[0] in keep]


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {path.relative_to(ROOT)}  ({len(rows)} rows)")


def generate(out: Path, days: int, seed: int) -> None:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days)

    print(f"Generating {days} days of Edge IQ demo data (seed={seed}) -> {out}")

    # ---------------------------------------------------------------- sites
    sites = [
        {
            "siteId": sid,
            "siteName": name,
            "siteType": stype,
            "capacityMld": cap,
            "region": region,
            "populationServed": rng.randint(8_000, 180_000),
        }
        for sid, name, stype, cap, region in SITES
    ]
    write_csv(out / "sites.csv", sites)

    # -------------------------------------------------- assets + devices
    assets: list[dict] = []
    devices: list[dict] = []
    for sid, _name, stype, _cap, _region in SITES:
        for suffix, cls, kw, crit, duty, manu, model in _rows_for_site(sid, stype):
            asset_id = f"{sid}-{suffix}"
            device_id = asset_id
            install = now - timedelta(days=rng.randint(400, 4000))
            assets.append(
                {
                    "assetId": asset_id,
                    "deviceId": device_id,
                    "siteId": sid,
                    "assetClass": cls,
                    "processArea": {
                        "pump": "pumping", "blower": "aeration", "valve": "flow control",
                        "analyzer": "water quality", "flowmeter": "metering",
                        "prv": "pressure management", "gateway": "edge infrastructure",
                    }[cls],
                    "manufacturer": manu,
                    "model": model,
                    "ratedPowerKw": kw,
                    "criticality": crit,
                    "dutyRole": duty,
                    "installDate": install.date().isoformat(),
                    "ageYears": round((now - install).days / 365.25, 1),
                }
            )

            is_flaky = device_id == SCENARIO["uc5_flaky_gateway"]
            is_offline = device_id == SCENARIO["uc5_offline_device"]
            is_flatline = device_id == SCENARIO["uc2_flatline_device"]
            cert_days = (
                SCENARIO["uc5_cert_expiry_days"] if is_flaky else rng.randint(45, 700)
            )
            devices.append(
                {
                    "deviceId": device_id,
                    "siteId": sid,
                    "docType": "device",
                    "deviceType": "gateway" if cls == "gateway" else (
                        "analyzer" if cls == "analyzer" else
                        "flowmeter" if cls == "flowmeter" else
                        "vfd" if cls in ("pump", "blower") else "sensor"
                    ),
                    "status": "offline" if is_offline else "degraded" if (is_flaky or is_flatline) else "online",
                    "firmwareVersion": rng.choice(["1.4.2", "1.5.0", "1.5.1", "2.0.0"]),
                    "edgeRuntimeVersion": rng.choice(["1.4.27", "1.5.11"]),
                    "moduleCount": rng.randint(2, 5) if cls == "gateway" else 1,
                    "minutesSinceLastMessage": 4320 if is_offline else rng.randint(1, 12),
                    "certDaysToExpiry": cert_days,
                    "connectivity": "cellular" if sid.startswith(("WELL", "RES")) else "ethernet",
                    "location": sid,
                    "provisionedAt": (now - timedelta(days=rng.randint(200, 1500))).isoformat(),
                }
            )
    write_csv(out / "assets.csv", assets)
    write_csv(out / "devices.csv", devices)

    # -------------------------------------------------------- telemetry
    telemetry: list[dict] = []
    hours = days * 24
    pump_assets = [a for a in assets if a["assetClass"] in ("pump", "blower")]
    for asset in pump_assets:
        device_id = asset["deviceId"]
        kw = float(asset["ratedPowerKw"])
        base_vib = rng.uniform(1.1, 2.1)
        base_temp = rng.uniform(42, 52)
        base_flow = kw * rng.uniform(1.6, 2.2)
        base_press = rng.uniform(4.0, 6.5)
        is_uc1 = device_id == SCENARIO["uc1_device"]
        is_uc4 = device_id == SCENARIO["uc4_device"]

        for h in range(hours):
            ts = start + timedelta(hours=h)
            days_ago = (now - ts).days
            diurnal = 1 + 0.18 * math.sin((ts.hour - 6) / 24 * 2 * math.pi)

            vib = base_vib * diurnal + rng.gauss(0, 0.07)
            temp = base_temp + 6 * (diurnal - 1) + rng.gauss(0, 0.8)
            if is_uc1 and days_ago <= SCENARIO["uc1_fault_start_day"]:
                # exponential bearing degradation toward the configured peak
                progress = 1 - days_ago / SCENARIO["uc1_fault_start_day"]
                vib += (SCENARIO["uc1_peak_vibration"] - base_vib) * (progress ** 2.2)
                temp += 14 * (progress ** 2.4)

            eff_penalty = 0.0
            if is_uc4:
                eff_penalty = SCENARIO["uc4_efficiency_loss_pct"] / 100.0

            flow = base_flow * diurnal * (1 - eff_penalty * 0.5) + rng.gauss(0, base_flow * 0.02)
            power = kw * 0.78 * diurnal * (1 + eff_penalty) + rng.gauss(0, kw * 0.015)

            telemetry.append(
                {
                    "timestamp": ts.isoformat(),
                    "deviceId": device_id,
                    "siteId": asset["siteId"],
                    "vibration_mm_s": round(max(vib, 0.05), 3),
                    "bearing_temp_c": round(temp, 2),
                    "motor_current_a": round(power * 1.9, 2),
                    "suction_pressure_bar": round(base_press * 0.35 + rng.gauss(0, 0.05), 3),
                    "discharge_pressure_bar": round(base_press * diurnal + rng.gauss(0, 0.08), 3),
                    "flow_m3h": round(max(flow, 0), 2),
                    "power_kw": round(max(power, 0), 2),
                    "runtime_hours": round(h * 0.82, 1),
                }
            )
    write_csv(out / "telemetry_hourly.csv", telemetry)

    # --------------------------------------------------- water quality
    wq: list[dict] = []
    analyzers = [a for a in assets if a["assetClass"] == "analyzer"]
    for asset in analyzers:
        device_id = asset["deviceId"]
        is_uc2 = device_id == SCENARIO["uc2_device"]
        is_flat = device_id == SCENARIO["uc2_flatline_device"]
        base_cl = rng.uniform(0.62, 0.95)
        for h in range(hours):
            ts = start + timedelta(hours=h)
            days_ago = (now - ts).days
            cl = base_cl + 0.06 * math.sin(h / 12) + rng.gauss(0, 0.025)
            if is_uc2:
                # progressive residual decay - long detention in a warm reservoir
                decay = max(0.0, 1 - days_ago / max(days, 1))
                cl = base_cl - (base_cl - SCENARIO["uc2_min_chlorine"]) * (decay ** 1.5)
            if is_flat and days_ago <= SCENARIO["uc2_flatline_day"]:
                cl = 0.52  # frozen analyzer output
            wq.append(
                {
                    "timestamp": ts.isoformat(),
                    "deviceId": device_id,
                    "siteId": asset["siteId"],
                    "chlorine_mg_l": round(max(cl, 0), 3),
                    "turbidity_ntu": round(abs(rng.gauss(0.16, 0.05)), 3),
                    "ph": round(rng.gauss(7.4, 0.12), 2),
                    "temperature_c": round(12 + 6 * math.sin(h / 4380 * 2 * math.pi) + rng.gauss(0, 0.4), 2),
                    "conductivity_us_cm": round(rng.gauss(465, 18), 1),
                }
            )
    write_csv(out / "water_quality.csv", wq)

    # ----------------------------------------------------- DMA flow / pressure
    dma_flow: list[dict] = []
    dma_pressure: list[dict] = []
    for dma_id in ("DMA-14", "DMA-22"):
        is_leak = dma_id == SCENARIO["uc3_dma"]
        baseline = SCENARIO["uc3_mnf_baseline"] if is_leak else rng.uniform(7.0, 9.5)
        for h in range(hours):
            ts = start + timedelta(hours=h)
            days_ago = (now - ts).days
            hour = ts.hour
            demand = 1.0 + 0.85 * math.sin((hour - 4) / 24 * 2 * math.pi) ** 2
            leak = 0.0
            if is_leak and days_ago <= SCENARIO["uc3_leak_start_day"]:
                progress = 1 - days_ago / SCENARIO["uc3_leak_start_day"]
                leak = (SCENARIO["uc3_mnf_peak"] - baseline) * progress
            flow = baseline * (1 + demand) + leak + rng.gauss(0, 0.4)
            dma_flow.append(
                {
                    "timestamp": ts.isoformat(),
                    "dmaId": dma_id,
                    "hourOfDay": hour,
                    "flowM3h": round(max(flow, 0), 2),
                    "baselineMnfM3h": round(baseline, 2),
                }
            )
            if h % 2 == 0:
                for idx, sensor in enumerate(("PS-A", "PS-B", "PS-C"), start=1):
                    drop = leak * 0.02 * (4 - idx) if is_leak else 0.0
                    dma_pressure.append(
                        {
                            "timestamp": ts.isoformat(),
                            "dmaId": dma_id,
                            "sensorId": f"{dma_id}-{sensor}",
                            "pressureBar": round(3.8 - 0.12 * idx - drop + rng.gauss(0, 0.03), 3),
                        }
                    )
    write_csv(out / "dma_flow.csv", dma_flow)
    write_csv(out / "dma_pressure.csv", dma_pressure)

    # ---------------------------------------------------------- energy
    energy = []
    for asset in pump_assets:
        for h in range(0, hours, 1):
            ts = start + timedelta(hours=h)
            tariff = 0.28 if 7 <= ts.hour < 11 or 17 <= ts.hour < 21 else 0.11
            kw = float(asset["ratedPowerKw"]) * 0.78 * (1 + 0.18 * math.sin((ts.hour - 6) / 24 * 2 * math.pi))
            energy.append(
                {
                    "timestamp": ts.isoformat(),
                    "deviceId": asset["deviceId"],
                    "siteId": asset["siteId"],
                    "energyKwh": round(kw, 2),
                    "tariffPerKwh": tariff,
                    "costUsd": round(kw * tariff, 2),
                    "carbonKgCo2e": round(kw * 0.371, 3),
                    "tariffPeriod": "peak" if tariff > 0.2 else "offpeak",
                }
            )
    write_csv(out / "energy_hourly.csv", energy)

    print("\nDone. Seeded scenarios:")
    for key, value in SCENARIO.items():
        print(f"  {key:26} {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Edge IQ demo data.")
    parser.add_argument("--days", type=int, default=21, help="days of history (default 21)")
    parser.add_argument("--seed", type=int, default=42, help="random seed for reproducibility")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "customdata")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    generate(args.out, args.days, args.seed)


if __name__ == "__main__":
    main()
