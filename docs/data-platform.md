# Data platform

Three stores, each chosen for a different access pattern: Cosmos DB for
operational records, Fabric Lakehouse for analytical history, Eventhouse for
live telemetry.

---

## Why three

A single store would be simpler and worse. The workloads genuinely differ:

| Need | Pattern | Store |
|---|---|---|
| "What is the status of WO-09001?" | Point read by key | Cosmos DB |
| "Vibration trend over 90 days" | Columnar scan | Lakehouse |
| "Chlorine in the last 15 minutes" | Time-ordered recent | Eventhouse |

Forcing 90-day trend analysis through Cosmos is expensive; forcing a point read
through a Delta table is slow. Each store handles what it is good at.

---

## Cosmos DB — operational

Database `edgeiq`, eight containers:

| Container | Partition key | Contents |
|---|---|---|
| `assets` | `/siteId` | Equipment master data |
| `telemetry_summary` | `/deviceId` | Rolled-up aggregates |
| `workorders` | `/siteId` | Maintenance work orders |
| `alarms` | `/deviceId` | Alarm history |
| `compliance` | `/siteId` | Regulatory records |
| `agent_memory` | `/conversationId` | Conversation state |
| `agent_traces` | `/conversationId` | Routing and tool traces |

Partition keys follow query shape. Asset and work-order queries are
overwhelmingly site-scoped; telemetry and alarms are device-scoped; agent state
is conversation-scoped. Getting this wrong produces cross-partition fan-out on
the hot path.

**`agent_traces` is the audit trail.** Every turn records the route, the agents
consulted, the tools called and the citations returned. In a regulated
environment, "the system recommended a shutdown" needs to be answerable with
evidence months later.

Access is via managed identity and RBAC. No keys anywhere.

---

## Fabric Lakehouse — analytical

Medallion architecture in OneLake.

```
EdgeIQ_Lakehouse/
  Files/
    bronze/telemetry/      raw landing, partitioned by date
  Tables/
    silver_telemetry       cleaned, typed, deduplicated
    gold_device_daily      daily aggregates per device/tag
    gold_asset_health      health scoring
    gold_dma_daily         DMA flow and NRW
    gold_energy_daily      energy and tariff exposure
    dim_site  dim_asset  dim_device  dim_tag  dim_regulatory_limit
```

**Bronze → Silver** (`fabric/notebooks/01_bronze_to_silver.py`): type coercion,
deduplication on `(deviceId, tag, timestamp)`, quality flagging. Bad readings are
flagged, not dropped — a sensor producing nonsense is itself a finding.

**Silver → Gold** (`02_silver_to_gold.py`): daily aggregates and the derived
tables agents actually query.

Gold exists because agents should not be computing 90-day rolling statistics
inside a reasoning loop. Precomputing turns a multi-second scan into a lookup,
and latency is what determines whether an operator uses the tool during an
incident or after it.

---

## OneLake

One logical data lake for the tenant. Practical consequences:

- **No copies.** Power BI, notebooks, the SQL endpoint and Edge IQ read the same
  Delta files.
- **Shortcuts.** Existing ADLS or S3 data can be mounted without ingestion.
- **One security model** across every engine.

Path convention:

```
abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{lakehouse}.Lakehouse/Tables/{table}
```

Derived in `config.py` from workspace and lakehouse names — never configured
directly, so it cannot drift from the names it is built from.

---

## Eventhouse — real-time

KQL database `EdgeIQ_Telemetry`, fed by an eventstream from IoT Hub.

```
IoT Hub → Eventstream → Eventhouse (hot, KQL)
                     └→ Lakehouse bronze (cold, Delta)
```

One stream, two sinks. Recent telemetry is queryable in seconds; the same data
lands in bronze for history. Definition: `fabric/eventstream/edgeiq-telemetry-eventstream.json`.

---

## The semantic ontology

`fabric/ontology/water_utility_ontology.yaml` maps business vocabulary to
physical schema. It is what lets an operator ask about "chlorine residual"
without knowing the tag is `CL2_RESIDUAL_MGL`.

```yaml
entities:
  - name: WaterQualityReading
    synonyms: [quality reading, sample, analyser reading]
    attributes:
      - name: chlorineResidual
        synonyms: [chlorine, free chlorine, residual, CL2]
        column: cl2_residual_mgl
        unit: mg/L
```

`resolve_entity()` matches in four tiers and reports which one hit:

1. Entity name
2. Entity synonym
3. Attribute `allowedValues`
4. Attribute name or synonym

Returned as `matchedVia: "<tier>:<detail>"`. When a question resolves oddly, the
trace shows exactly which rule fired — the difference between a fixable mapping
and an unexplainable answer.

Extending the ontology is the highest-leverage change available: adding a
synonym improves every future question using that term, with no code change.

---

## Demo data

Two generators produce a coherent estate:

```bash
python data/generators/generate_reference.py    # sites, assets, devices, tags
python data/generators/generate_telemetry.py --days 30 --seed 7
```

19 CSVs in `data/customdata/`, 19 Parquet files in `data/lakehouse/`.
Deterministic under `--seed`, so a demo reproduces exactly.

Faults are seeded to match the five use cases — the `SCENARIO` dict in
`generate_telemetry.py` (lines 37–63) holds every parameter. Change a value
there and both the data and the scenario change together.

Notably, the data includes **both** a genuine chlorine breach and a flatlined
analyser. A system that cannot tell them apart will either miss a public health
event or dispatch a crew to a working sensor.

---

## Adding a data source

1. Route it into the eventstream, or land Delta files in bronze.
2. Extend `01_bronze_to_silver.py` with its cleaning rules.
3. Add gold aggregates if agents will query it frequently.
4. Add entities and synonyms to the ontology — otherwise natural language cannot
   reach it.
5. Expose it through an MCP tool.
6. Grant that tool to the agents that need it.

Skipping step 4 is the common mistake: the data is present, queryable, and
invisible to every question a human would actually ask.

---

## Related

- [Architecture](./architecture.md)
- [MCP servers](./mcp-servers.md)
- [Configuration reference](./configuration.md)
