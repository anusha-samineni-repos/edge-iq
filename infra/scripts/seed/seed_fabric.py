#!/usr/bin/env python3
"""
Seed the Fabric Lakehouse reference tables in OneLake.

The gold notebook (``fabric/notebooks/02_silver_to_gold.py``) joins telemetry
against a set of ``ref_*`` Delta tables - sites, assets, devices, failure
modes, regulatory limits, spare parts, crew availability. Those tables are
reference data, not streamed telemetry, so they are seeded once here rather
than flowing through the Eventstream.

Three ways to land the data, in order of preference:

  --mode onelake   Write Parquet directly to the Lakehouse Files area over
                   ABFSS, then the notebook converts to Delta. Needs only
                   azure-identity + pandas + pyarrow. This is the default.

  --mode notebook  Emit a PySpark notebook that reads the CSVs from Files/ and
                   writes Delta tables. Use when you would rather run the load
                   inside Fabric with full Spark semantics.

  --mode local     Write Parquet to a local folder. Used by the smoke tests and
                   by demo mode, where no Fabric workspace exists at all.

Usage
-----
    python seed_fabric.py --mode local --out data/lakehouse
    python seed_fabric.py --mode notebook
    python seed_fabric.py --mode onelake \
        --workspace EdgeIQ-WaterUtility --lakehouse EdgeIQ_Lakehouse
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data" / "customdata"
NOTEBOOK_OUT = REPO_ROOT / "fabric" / "notebooks" / "00_seed_reference_tables.py"

# CSV stem -> Delta table name. The notebook reads these exact names, so this
# mapping is the contract between the seeder and the gold layer.
REFERENCE_TABLES: dict[str, str] = {
    "sites": "ref_sites",
    "assets": "ref_assets",
    "devices": "ref_devices",
    "failure_modes": "ref_failure_modes",
    "regulatory_limits": "ref_regulatory_limits",
    "spare_parts": "ref_spare_parts",
    "crew_availability": "ref_crew_availability",
    "deployments": "ref_deployments",
    "module_twins": "ref_module_twins",
    "calibrations": "ref_calibrations",
}

# Bulk history. In a real deployment these arrive via Eventstream into bronze;
# seeding them lets a fresh environment demo immediately instead of waiting
# days for enough telemetry to show a trend.
HISTORY_TABLES: dict[str, str] = {
    "telemetry_hourly": "bronze_telemetry",
    "water_quality": "bronze_water_quality",
    "dma_flow": "bronze_dma_flow",
    "dma_pressure": "bronze_dma_pressure",
    "energy_hourly": "bronze_energy",
    "alarms": "bronze_alarms",
    "connectivity_events": "bronze_connectivity",
    "workorders": "ref_workorders",
    "nrw_monthly": "ref_nrw_monthly",
}


def _load(stem: str):
    import pandas as pd  # type: ignore

    path = DATA_DIR / f"{stem}.csv"
    if not path.exists():
        raise SystemExit(
            f"Missing {path}.\nRun the generators first:\n"
            f"  python data/generators/generate_reference.py\n"
            f"  python data/generators/generate_telemetry.py"
        )
    return pd.read_csv(path)


def _tables(include_history: bool) -> dict[str, str]:
    t = dict(REFERENCE_TABLES)
    if include_history:
        t.update(HISTORY_TABLES)
    return t


# --------------------------------------------------------------------------- #

def write_local(out: Path, include_history: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for stem, table in _tables(include_history).items():
        df = _load(stem)
        target = out / f"{table}.parquet"
        df.to_parquet(target, index=False)
        total += len(df)
        print(f"  {len(df):>7} rows -> {target.name}")
    print(f"\nWrote {total} rows to {out}")
    return 0


def write_onelake(workspace: str, lakehouse: str, include_history: bool,
                  subfolder: str) -> int:
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore
        from azure.storage.filedatalake import DataLakeServiceClient  # type: ignore
    except ImportError:
        raise SystemExit(
            "azure-storage-file-datalake and azure-identity are required for "
            "--mode onelake.\n  pip install azure-storage-file-datalake "
            "azure-identity pyarrow\n"
            "Or use --mode local / --mode notebook."
        )
    import io

    service = DataLakeServiceClient(
        account_url="https://onelake.dfs.fabric.microsoft.com",
        credential=DefaultAzureCredential(),
    )
    # In OneLake the workspace is the filesystem and the lakehouse is a
    # directory within it.
    fs = service.get_file_system_client(workspace)
    base = f"{lakehouse}.Lakehouse/Files/{subfolder}"

    total = 0
    for stem, table in _tables(include_history).items():
        df = _load(stem)
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        data = buf.getvalue()

        file_client = fs.get_file_client(f"{base}/{table}.parquet")
        file_client.upload_data(data, overwrite=True)
        total += len(df)
        print(f"  {len(df):>7} rows -> {base}/{table}.parquet")

    print(f"\nUploaded {total} rows to OneLake.")
    print(f"Now run fabric/notebooks/00_seed_reference_tables.py in Fabric to "
          f"convert Files/{subfolder}/*.parquet into managed Delta tables.")
    return 0


NOTEBOOK_TEMPLATE = '''"""
00 - Seed reference and history tables (GENERATED)

Generated by infra/scripts/seed/seed_fabric.py. Do not edit by hand; re-run the
seeder to regenerate.

Reads the Parquet files uploaded to Files/{subfolder}/ and registers them as
managed Delta tables in the Lakehouse. Run this once after `azd up`, before
01_bronze_to_silver and 02_silver_to_gold.

Overwrite semantics are deliberate: reference data is a full snapshot, not an
increment. Re-running this resets the reference layer to exactly what is in
data/customdata, which is what you want when re-seeding a demo environment.
"""

SOURCE_FOLDER = "Files/{subfolder}"

TABLES = {tables!r}

for table_name in TABLES:
    path = f"{{SOURCE_FOLDER}}/{{table_name}}.parquet"
    try:
        df = spark.read.parquet(path)
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP  {{table_name}}: {{exc}}")
        continue

    (df.write
       .format("delta")
       .mode("overwrite")
       .option("overwriteSchema", "true")
       .saveAsTable(table_name))
    print(f"OK    {{table_name}}: {{df.count()}} rows")

print("\\nReference seed complete. Next: 01_bronze_to_silver.")
'''


def write_notebook(include_history: bool, subfolder: str) -> int:
    tables = sorted(_tables(include_history).values())
    NOTEBOOK_OUT.parent.mkdir(parents=True, exist_ok=True)
    NOTEBOOK_OUT.write_text(
        NOTEBOOK_TEMPLATE.format(subfolder=subfolder, tables=tables),
        encoding="utf-8",
    )
    print(f"  wrote {NOTEBOOK_OUT.relative_to(REPO_ROOT)} ({len(tables)} tables)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the Fabric Lakehouse.")
    ap.add_argument("--mode", choices=["local", "onelake", "notebook"],
                    default="local")
    ap.add_argument("--workspace",
                    default=os.getenv("FABRIC_WORKSPACE_NAME", "EdgeIQ-WaterUtility"))
    ap.add_argument("--lakehouse",
                    default=os.getenv("FABRIC_LAKEHOUSE_NAME", "EdgeIQ_Lakehouse"))
    ap.add_argument("--subfolder", default="seed",
                    help="Folder under Files/ to stage Parquet into.")
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "lakehouse"),
                    help="Output folder for --mode local.")
    ap.add_argument("--reference-only", action="store_true",
                    help="Skip the bulk telemetry history tables.")
    args = ap.parse_args()

    include_history = not args.reference_only
    tables = _tables(include_history)

    print("Edge IQ - Fabric Lakehouse seed")
    print(f"  mode       {args.mode}")
    print(f"  source     {DATA_DIR}")
    print(f"  tables     {len(tables)} "
          f"({len(REFERENCE_TABLES)} reference"
          f"{f' + {len(HISTORY_TABLES)} history' if include_history else ''})")
    if args.mode == "onelake":
        print(f"  workspace  {args.workspace}")
        print(f"  lakehouse  {args.lakehouse}")

    # The notebook is always regenerated - it is the instruction sheet for the
    # Fabric side regardless of how the Parquet got there.
    write_notebook(include_history, args.subfolder)

    if args.mode == "notebook":
        return 0
    if args.mode == "local":
        return write_local(Path(args.out), include_history)
    return write_onelake(args.workspace, args.lakehouse, include_history,
                         args.subfolder)


if __name__ == "__main__":
    sys.exit(main())
