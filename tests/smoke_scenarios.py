"""
Validate the demo scenarios against the things they claim about.

A demo script is a promise: ask this question, see this answer. The promise
breaks silently if someone retunes the generator, renames an agent or drops an
MCP server - the scenario JSON still parses, it just describes a system that no
longer exists. These checks tie each scenario back to the generator's SCENARIO
dict, the agent registry, the MCP server modules and the generated CSVs, so
that drift fails here instead of in front of an audience.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "data" / "scenarios"
DATA = REPO / "data" / "customdata"
GENERATOR = REPO / "data" / "generators" / "generate_telemetry.py"
REGISTRY = REPO / "src" / "api" / "python" / "agents" / "registry.py"
SERVERS = REPO / "src" / "mcp_servers" / "servers"

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def load_generator_scenario() -> dict:
    """Import the generator's SCENARIO dict without running generation."""
    spec = importlib.util.spec_from_file_location("_gen", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_gen"] = module  # dataclasses resolve via sys.modules
    spec.loader.exec_module(module)
    return module.SCENARIO


def known_agents() -> set[str]:
    """Agent identifiers declared in the registry (AgentSpec uses `name=`)."""
    text = REGISTRY.read_text(encoding="utf-8")
    return set(re.findall(r'^\s*name\s*=\s*"([a-z][a-z0-9-]+)"', text, re.MULTILINE))


def device_ids() -> set[str]:
    ids: set[str] = set()
    for name in ("devices.csv", "assets.csv"):
        path = DATA / name
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                for key in ("deviceId", "assetId", "device_id", "id"):
                    if row.get(key):
                        ids.add(row[key])
    return ids


def csv_column_values(filename: str, *columns: str) -> set[str]:
    path = DATA / filename
    if not path.is_file():
        return set()
    out: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for col in columns:
                if row.get(col):
                    out.add(row[col])
    return out


def main() -> int:
    print("Edge IQ - demo scenario checks")

    files = sorted(SCENARIOS.glob("uc*.json"))
    print(f"\nDiscovery")
    check("scenario files found", len(files) == 5, f"found {len(files)}")
    check("schema present", (SCENARIOS / "scenario.schema.json").is_file())

    if not files:
        return 1

    gen = load_generator_scenario()
    agents = known_agents()
    devices = device_ids()
    dmas = csv_column_values("dma_flow.csv", "dmaId", "dma_id")
    sites = csv_column_values("sites.csv", "siteId", "site_id")
    servers = {p.stem for p in SERVERS.glob("*.py") if p.stem != "__init__"}

    # Every device named in any generator scenario key, for cross-checking.
    seeded_devices = {v for k, v in gen.items()
                      if isinstance(v, str) and re.match(r"^[A-Z]", v)}

    seen_ids: set[str] = set()

    for path in files:
        print(f"\n{path.name}")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            check(f"{path.name} parses", False, str(exc))
            continue

        check("valid JSON with required keys",
              all(k in doc for k in ("id", "title", "summary", "primaryAgent", "script")))

        uc = doc.get("id", "?")
        check("unique id", uc not in seen_ids, f"duplicate {uc}")
        seen_ids.add(uc)

        # Agents must exist in the registry.
        primary = doc.get("primaryAgent", "")
        check(f"primaryAgent '{primary}' is registered",
              primary in agents,
              f"known: {sorted(agents)}")

        unknown = [a for a in doc.get("supportingAgents", []) if a not in agents]
        check("supporting agents are registered", not unknown, f"unknown: {unknown}")

        # MCP servers must exist as modules.
        missing_srv = [s for s in doc.get("mcpServers", []) if s not in servers]
        check("referenced MCP servers exist", not missing_srv,
              f"missing: {missing_srv} (have {sorted(servers)})")

        # Faults must reference real, seeded entities.
        for key in ("seededFault", "secondFault"):
            fault = doc.get(key)
            if not fault:
                continue

            if dev := fault.get("device"):
                check(f"{key}: device {dev} exists in the dataset",
                      dev in devices or not devices,
                      "not found in devices.csv/assets.csv")
                check(f"{key}: device {dev} is a seeded fault",
                      dev in seeded_devices,
                      "not referenced by generate_telemetry.SCENARIO")

            if dma := fault.get("dma"):
                check(f"{key}: DMA {dma} exists",
                      dma in dmas or not dmas,
                      "not found in dma_flow.csv")

            if site := fault.get("site"):
                check(f"{key}: site {site} exists",
                      site in sites or not sites,
                      "not found in sites.csv")

            # A tunableIn pointer that names a missing key is worse than none.
            if tunable := fault.get("tunableIn"):
                if m := re.search(r"SCENARIO\['(\w+)'\]", tunable):
                    check(f"{key}: SCENARIO['{m.group(1)}'] exists",
                          m.group(1) in gen)
                path_part = tunable.split(" ->")[0].strip()
                check(f"{key}: tunableIn path exists",
                      (REPO / path_part).exists(), path_part)

        # The script is the demo itself.
        script = doc.get("script", [])
        check("script has steps", len(script) >= 1)
        check("steps are numbered from 1 in order",
              [s.get("step") for s in script] == list(range(1, len(script) + 1)))
        check("every step has a question and an expectation",
              all(s.get("ask") and s.get("expect") for s in script))

    # Cross-file: the five use cases should be covered exactly once.
    print("\nCoverage")
    check("UC1-UC5 all covered",
          seen_ids == {"UC1", "UC2", "UC3", "UC4", "UC5"},
          f"found {sorted(seen_ids)}")

    # Every seeded fault should appear in at least one scenario, otherwise we
    # generated a fault nobody demonstrates. Faults are keyed by device or by
    # DMA, so collect both.
    demoed = set()
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for key in ("seededFault", "secondFault"):
            if fault := doc.get(key):
                demoed.update(v for v in (fault.get("device"), fault.get("dma")) if v)

    orphans = {d for d in seeded_devices if re.match(r"^[A-Z]+-\d", d)} - demoed
    check("every seeded fault entity is demonstrated",
          not orphans, f"undemonstrated: {sorted(orphans)}")

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
