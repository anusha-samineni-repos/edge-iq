#!/usr/bin/env python3
"""
Seed Cosmos DB from the generated CSVs in data/customdata.

Cosmos holds the *operational registry* - the things agents look up by key and
occasionally write to. It deliberately does NOT hold bulk telemetry; that lives
in the Fabric Lakehouse (gold) and Eventhouse (hot path). What Cosmos holds is:

    assets            asset master, partitioned by /siteId
    telemetry_summary per-device daily rollups, partitioned by /deviceId, TTL'd
    workorders        maintenance work, partitioned by /siteId
    alarms            active and historic alarms, partitioned by /deviceId
    compliance        water quality compliance records, partitioned by /siteId

Idempotent: every document has a deterministic id, and we upsert. Re-running
this refreshes the data rather than duplicating it.

Usage
-----
    python seed_cosmos.py                        # uses AZURE_COSMOS_ENDPOINT
    python seed_cosmos.py --endpoint https://...
    python seed_cosmos.py --dry-run              # validate shaping, write nothing
    python seed_cosmos.py --only assets,alarms
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data" / "customdata"

# Containers as created by infra/bicep/modules/data/cosmos-db-nosql.bicep.
# Keep these two in sync - the partition keys here must match the Bicep.
CONTAINERS: dict[str, str] = {
    "assets": "/siteId",
    "telemetry_summary": "/deviceId",
    "workorders": "/siteId",
    "alarms": "/deviceId",
    "compliance": "/siteId",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _read_csv(name: str) -> list[dict[str, str]]:
    path = DATA_DIR / f"{name}.csv"
    if not path.exists():
        raise SystemExit(
            f"Missing {path}.\n"
            f"Run the generators first:\n"
            f"  python data/generators/generate_reference.py\n"
            f"  python data/generators/generate_telemetry.py"
        )
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _num(value: str | None) -> float | int | None:
    """Coerce a CSV cell to a number, or None. Cosmos indexes numbers properly;
    numbers-as-strings silently break every range query an agent writes."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    return int(f) if f.is_integer() and abs(f) < 2**53 else f


def _stable_id(*parts: str) -> str:
    """Deterministic id so re-seeding upserts rather than duplicates."""
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if v is not None and v != ""}


# --------------------------------------------------------------------------- #
# shaping - one function per container
# --------------------------------------------------------------------------- #

def shape_assets() -> Iterator[dict[str, Any]]:
    """Asset master, enriched with its edge device and open-alarm count.

    Agents ask 'what is this asset and is it healthy' as one question, so we
    denormalise the device onto the asset. One point read instead of three.
    """
    devices = {d["deviceId"]: d for d in _read_csv("devices")}
    sites = {s["siteId"]: s for s in _read_csv("sites")}

    open_alarms: dict[str, int] = defaultdict(int)
    for a in _read_csv("alarms"):
        if a.get("state") == "active":
            open_alarms[a["deviceId"]] += 1

    open_wos: dict[str, int] = defaultdict(int)
    for w in _read_csv("workorders"):
        if w.get("status") in {"open", "in_progress", "scheduled"}:
            open_wos[w["assetId"]] += 1

    for row in _read_csv("assets"):
        device = devices.get(row["deviceId"], {})
        site = sites.get(row["siteId"], {})
        yield _clean({
            "id": row["assetId"],
            "docType": "asset",
            "assetId": row["assetId"],
            "siteId": row["siteId"],
            "deviceId": row["deviceId"],
            "assetClass": row["assetClass"],
            "processArea": row.get("processArea"),
            "manufacturer": row.get("manufacturer"),
            "model": row.get("model"),
            "ratedPowerKw": _num(row.get("ratedPowerKw")),
            "criticality": row.get("criticality"),
            "dutyRole": row.get("dutyRole"),
            "installDate": row.get("installDate"),
            "ageYears": _num(row.get("ageYears")),
            "site": _clean({
                "siteName": site.get("siteName"),
                "siteType": site.get("siteType"),
                "region": site.get("region"),
                "capacityMld": _num(site.get("capacityMld")),
            }) or None,
            "edgeDevice": _clean({
                "deviceType": device.get("deviceType"),
                "status": device.get("status"),
                "connectivity": device.get("connectivity"),
                "firmwareVersion": device.get("firmwareVersion"),
                "edgeRuntimeVersion": device.get("edgeRuntimeVersion"),
                "moduleCount": _num(device.get("moduleCount")),
                "certDaysToExpiry": _num(device.get("certDaysToExpiry")),
                "minutesSinceLastMessage": _num(device.get("minutesSinceLastMessage")),
            }) or None,
            "openAlarmCount": open_alarms.get(row["deviceId"], 0),
            "openWorkOrderCount": open_wos.get(row["assetId"], 0),
            "_seededUtc": datetime.now(timezone.utc).isoformat(),
        })


def shape_alarms() -> Iterator[dict[str, Any]]:
    for row in _read_csv("alarms"):
        yield _clean({
            "id": row["alarmId"],
            "docType": "alarm",
            "alarmId": row["alarmId"],
            "deviceId": row["deviceId"],
            "siteId": row["siteId"],
            "severity": row.get("severity"),
            "title": row.get("title"),
            "detail": row.get("detail"),
            "state": row.get("state"),
            "raisedAt": row.get("raisedAt"),
            "clearedAt": row.get("clearedAt") or None,
            "source": row.get("source"),
            "isOpen": row.get("state") == "active",
        })


def shape_workorders() -> Iterator[dict[str, Any]]:
    for row in _read_csv("workorders"):
        yield _clean({
            "id": row["workOrderId"],
            "docType": "workorder",
            "workOrderId": row["workOrderId"],
            "assetId": row["assetId"],
            "siteId": row["siteId"],
            "title": row.get("title"),
            "description": row.get("description"),
            "priority": row.get("priority"),
            "status": row.get("status"),
            "failureMode": row.get("failureMode"),
            "createdAt": row.get("createdAt"),
            "completedAt": row.get("completedAt") or None,
            "downtimeHours": _num(row.get("downtimeHours")),
            "labourHours": _num(row.get("labourHours")),
            "crewId": row.get("crewId"),
            "costUsd": _num(row.get("costUsd")),
            "isOpen": row.get("status") in {"open", "in_progress", "scheduled"},
            # Seeded history is by definition human-raised. Anything Edge IQ
            # proposes later carries origin='edge-iq' plus an approvedBy field,
            # so an auditor can always separate the two populations.
            "origin": "historical",
        })


def shape_compliance() -> Iterator[dict[str, Any]]:
    """Daily per-sample-point water quality compliance rollup.

    Compliance is assessed daily, not per reading - that is how the regulator
    asks the question, so that is the grain we store. The `analyserSuspect`
    flag is the important one: a flatlined analyser produces a perfect
    compliance record, and without this flag that record looks like good news.
    """
    limits = {r["parameter"]: r for r in _read_csv("regulatory_limits")
              if r.get("jurisdiction") == "US-EPA"}
    cl_limit = limits.get("chlorine_mg_l", {})
    cl_min = _num(cl_limit.get("minValue")) or 0.2
    cl_max = _num(cl_limit.get("maxValue")) or 4.0

    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}

    for row in _read_csv("water_quality"):
        ts = row.get("timestamp", "")
        day = ts[:10]
        if not day:
            continue
        key = (row["siteId"], row["deviceId"], day)
        b = buckets.setdefault(key, {
            "siteId": row["siteId"],
            "deviceId": row["deviceId"],
            "samplePointId": row.get("samplePointId") or row["deviceId"],
            "day": day,
            "values": [],
            "turbidity": [],
            "ph": [],
        })
        cl = _num(row.get("chlorine_mg_l"))
        if cl is not None:
            b["values"].append(cl)
        tu = _num(row.get("turbidity_ntu"))
        if tu is not None:
            b["turbidity"].append(tu)
        ph = _num(row.get("ph"))
        if ph is not None:
            b["ph"].append(ph)

    for (site_id, device_id, day), b in buckets.items():
        vals: list[float] = b["values"]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        breaches = sum(1 for v in vals if v < cl_min or v > cl_max)
        # A reading that never moves across a whole day is not a measurement.
        # But only call it a *suspect analyser* when it is flat INSIDE the
        # compliant band - that is the case that masquerades as good news. A
        # reading pinned at a breach level is a real excursion (a decaying
        # residual sitting on its floor), and must stay an excursion.
        invariant = len(vals) >= 12 and (hi - lo) < 0.005
        flat = invariant and breaches == 0
        yield _clean({
            "id": _stable_id("compliance", site_id, device_id, day),
            "docType": "compliance",
            "siteId": site_id,
            "deviceId": device_id,
            "samplePointId": b["samplePointId"],
            "parameter": "chlorine_mg_l",
            "date": day,
            "sampleCount": len(vals),
            "minValue": round(lo, 4),
            "maxValue": round(hi, 4),
            "meanValue": round(sum(vals) / len(vals), 4),
            "limitMin": cl_min,
            "limitMax": cl_max,
            "breachCount": breaches,
            "compliant": breaches == 0 and not flat,
            "analyserSuspect": flat,
            "assessmentNote": (
                "Analyser output invariant across the day - treat as no data, "
                "not as compliance. Verify per SOP-CL2-003 before reporting."
                if flat else None
            ),
            "turbidityMaxNtu": round(max(b["turbidity"]), 4) if b["turbidity"] else None,
            "phMin": round(min(b["ph"]), 3) if b["ph"] else None,
            "phMax": round(max(b["ph"]), 3) if b["ph"] else None,
            "citation": cl_limit.get("citation"),
        })


def shape_telemetry_summary() -> Iterator[dict[str, Any]]:
    """Per-device, per-day, per-tag rollup.

    This container is TTL'd (see the Bicep). It exists so an agent can answer
    "how has this looked over the last month" with one Cosmos query instead of
    a Fabric round trip. Long-range analysis still belongs in the Lakehouse.
    """
    TAGS = (
        "vibration_mm_s", "bearing_temp_c", "motor_current_a",
        "flow_m3_h", "discharge_pressure_bar", "suction_pressure_bar",
        "power_kw", "speed_rpm",
    )
    buckets: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    meta: dict[str, str] = {}

    for row in _read_csv("telemetry_hourly"):
        day = row.get("timestamp", "")[:10]
        if not day:
            continue
        device_id = row["deviceId"]
        meta[device_id] = row.get("siteId", "")
        for tag in TAGS:
            v = _num(row.get(tag))
            if v is not None:
                buckets[(device_id, day, tag)].append(float(v))

    by_device_day: dict[tuple[str, str], dict[str, Any]] = {}
    for (device_id, day, tag), vals in buckets.items():
        entry = by_device_day.setdefault((device_id, day), {})
        n = len(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n if n > 1 else 0.0
        entry[tag] = {
            "n": n,
            "mean": round(mean, 4),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "std": round(var ** 0.5, 4),
        }

    for (device_id, day), tags in by_device_day.items():
        yield {
            "id": _stable_id("tsum", device_id, day),
            "docType": "telemetry_summary",
            "deviceId": device_id,
            "siteId": meta.get(device_id, ""),
            "date": day,
            "tags": tags,
            # Expected 24 hourly samples per day. Anything materially below
            # that means the answer built on this row is partial.
            "expectedSamples": 24,
            "observedSamples": max((t["n"] for t in tags.values()), default=0),
            "completenessPct": round(
                100.0 * max((t["n"] for t in tags.values()), default=0) / 24.0, 1
            ),
        }


SHAPERS: dict[str, Callable[[], Iterable[dict[str, Any]]]] = {
    "assets": shape_assets,
    "alarms": shape_alarms,
    "workorders": shape_workorders,
    "compliance": shape_compliance,
    "telemetry_summary": shape_telemetry_summary,
}


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="Seed Cosmos DB for Edge IQ.")
    ap.add_argument("--endpoint", default=os.getenv("AZURE_COSMOS_ENDPOINT", ""))
    ap.add_argument("--database", default=os.getenv("AZURE_COSMOS_DATABASE", "edgeiq"))
    ap.add_argument("--only", default="", help="Comma-separated container subset.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Shape and validate documents, write nothing.")
    ap.add_argument("--emit", default="",
                    help="Write shaped documents to this directory as JSONL.")
    args = ap.parse_args()

    selected = [c.strip() for c in args.only.split(",") if c.strip()] or list(SHAPERS)
    unknown = [c for c in selected if c not in SHAPERS]
    if unknown:
        raise SystemExit(f"Unknown container(s): {', '.join(unknown)}")

    print(f"Edge IQ - Cosmos seed")
    print(f"  source     {DATA_DIR}")
    print(f"  database   {args.database}")
    print(f"  containers {', '.join(selected)}")

    shaped: dict[str, list[dict[str, Any]]] = {}
    for container in selected:
        docs = list(SHAPERS[container]())
        shaped[container] = docs
        print(f"  shaped {len(docs):>6} -> {container}")

    if args.emit:
        out = Path(args.emit)
        out.mkdir(parents=True, exist_ok=True)
        for container, docs in shaped.items():
            p = out / f"{container}.jsonl"
            with p.open("w", encoding="utf-8") as fh:
                for d in docs:
                    fh.write(json.dumps(d, separators=(",", ":")) + "\n")
            print(f"  wrote {p}")

    if args.dry_run:
        print("\nDry run - nothing written to Cosmos.")
        return 0

    if not args.endpoint:
        print(
            "\nNo Cosmos endpoint.\n"
            "  Set AZURE_COSMOS_ENDPOINT or pass --endpoint.\n"
            "  Edge IQ runs fully in demo mode without Cosmos "
            "(EDGEIQ_DEMO_MODE=true reads these CSVs directly),\n"
            "  so this is only needed for a dev/prod deployment."
        )
        return 0

    try:
        from azure.cosmos import CosmosClient, PartitionKey  # type: ignore
        from azure.identity import DefaultAzureCredential  # type: ignore
    except ImportError:
        raise SystemExit(
            "azure-cosmos and azure-identity are required to write.\n"
            "  pip install azure-cosmos azure-identity\n"
            "Or re-run with --dry-run / --emit to inspect the shaped documents."
        )

    client = CosmosClient(args.endpoint, credential=DefaultAzureCredential())
    db = client.create_database_if_not_exists(args.database)

    total = 0
    for container_name in selected:
        container = db.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path=CONTAINERS[container_name]),
        )
        docs = shaped[container_name]
        for i, doc in enumerate(docs, 1):
            container.upsert_item(doc)
            if i % 500 == 0:
                print(f"    {container_name}: {i}/{len(docs)}")
        total += len(docs)
        print(f"  upserted {len(docs):>6} -> {container_name}")

    print(f"\nDone. {total} documents upserted into '{args.database}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
