# MCP servers and tools

Agents reach operational systems only through MCP (Model Context Protocol)
servers. Five servers expose 36 typed tools; a gateway fronts them on a single
endpoint.

Source: `src/mcp_servers/`

---

## Why MCP rather than direct calls

Three reasons that matter in an OT-adjacent environment:

1. **Entitlements are enforceable.** Tools are granted per agent. There is no
   shared client object an agent can reach past its allocation —
   `knowledge-navigator` has no tools at all, and that is structural rather
   than a matter of prompt discipline.
2. **The boundary is typed and auditable.** Every system interaction is a named
   tool call with a declared schema, which is exactly what a regulated
   environment needs in a trace.
3. **Reuse beyond this project.** The servers are standalone. Any MCP client —
   Copilot Studio, Claude Desktop, VS Code, another accelerator — can consume
   them without importing Edge IQ.

---

## The five servers

### `edge_fleet` — device estate

Operating the edge infrastructure itself.

| Tool | Purpose |
|---|---|
| `list_devices` | Inventory, filterable by site and state |
| `get_device` | Single device record |
| `get_module_twin` | Desired vs reported configuration (drift detection) |
| `get_connectivity_history` | Disconnect events over a window |
| `get_certificate_status` | Expiry horizon across the estate |
| `get_deployment_status` | IoT Edge deployment rollout state |
| `get_fleet_summary` | Aggregate health |

Twin drift and certificate expiry are here because edge estates fail quietly. A
gateway that stops reporting raises no alarm — it produces silence, which looks
indistinguishable from calm.

---

### `historian` — time series

The general-purpose telemetry interface.

| Tool | Purpose |
|---|---|
| `list_tags` | Available tags for a device |
| `get_timeseries` | Series with downsampling (`max_points`) |
| `get_statistics` | Min/max/mean/stddev over a window |
| `detect_anomalies` | Sigma-threshold outliers |
| `compare_devices` | Same tag across multiple devices |
| `get_alarms` | Alarm history |
| `get_data_quality` | Completeness and staleness |

`get_data_quality` exists because a question about a signal is often really a
question about whether the signal can be trusted. Reasoning over a stale series
produces a confident answer about data that stopped arriving three days ago.

---

### `asset_registry` — equipment master data

| Tool | Purpose |
|---|---|
| `get_asset` | Single asset record |
| `list_assets` | Filter by site, class, criticality |
| `get_asset_hierarchy` | Site → system → asset tree |
| `get_vibration_limits` | **ISO 10816-3 bands for this machine** |
| `get_maintenance_history` | Past interventions |
| `get_failure_modes` | Known failure modes by asset class |
| `get_spare_parts` | Parts and stock |

`get_vibration_limits` is the tool that makes UC1 correct. ISO 10816-3 bands
depend on rated power:

| Rated power | Zone A/B | Zone B/C | Zone C/D |
|---|---|---|---|
| ≤ 15 kW | 1.4 | 2.8 | 4.5 |
| 15–75 kW | 2.3 | 4.5 | 7.1 |
| > 75 kW | 3.5 | 7.1 | 11.0 |

7.9 mm/s is zone D on a 75 kW machine and only zone C on a 90 kW one. An agent that
assumes a threshold instead of reading rated power will be confidently wrong
about half the estate.

---

### `water_quality` — quality and network

| Tool | Purpose |
|---|---|
| `get_readings` | Quality parameter series |
| `get_regulatory_limits` | Limits by parameter and jurisdiction |
| `check_compliance` | Breach evaluation over a window |
| `assess_sensor_validity` | **Is the instrument actually working?** |
| `get_dma_flow` | District metered area flow |
| `get_pressure_profile` | Network pressure |
| `calculate_nrw` | Non-revenue water |

`assess_sensor_validity` is the UC2 tool. It distinguishes a flatlined analyser
from a genuine excursion, and the rule is narrower than it first appears:

> An analyser is suspect when readings are **invariant AND there are zero
> breaches**.

The breach condition is essential. A decayed chlorine residual sits pinned at
its floor — invariant, but genuinely breaching. Without that condition the
detector dismisses a real public health event as an instrument fault. This was
a live bug during development, caught because the seeded data contains both
cases.

`get_regulatory_limits` takes a `jurisdiction` (default `US-EPA`) so the same
deployment can serve multiple regulatory regimes.

---

### `work_order` — maintenance (the only writer)

| Tool | Write | Purpose |
|---|---|---|
| `list_work_orders` | | Filter by site, status, priority |
| `get_work_order` | | Single record |
| `calculate_priority` | | Criticality × consequence |
| `check_crew_availability` | | Scheduling constraints |
| `estimate_cost` | | Labour, parts, downtime |
| `create_work_order` | **yes** | Raise a work order |
| `update_work_order` | **yes** | Modify |
| `assign_work_order` | **yes** | Assign to a crew |

All three write tools are gated by two independent keys:

```python
create_work_order(..., approved=True)   # key 1: explicit approval
EDGEIQ_ALLOW_WRITES=true                # key 2: deployment permits writes
```

Missing either returns:

```json
{ "committed": false, "approvalRequired": true, "draft": { ... } }
```

Two keys because they fail differently. A model can be argued into setting
`approved=true`; it cannot change a deployment environment variable.

---

## The gateway

`src/mcp_servers/gateway.py` mounts all five servers behind one Starlette app.

```
GET  /health              readiness + active backend
     /edge-fleet/...      per-server mount points
     /historian/...
     /asset-registry/...
     /water-quality/...
     /work-order/...
```

Run it:

```bash
python -m uvicorn mcp_servers.gateway:app --port 8080
```

Point the API at it with `EDGEIQ_MCP_GATEWAY_URL`.

---

## Data backends

`datasource.py` selects a backend at startup and reports it on `/health`:

| Backend | When | Source |
|---|---|---|
| `cosmos` | `AZURE_COSMOS_ENDPOINT` set | Cosmos DB |
| `fabric` | Fabric endpoints set | Lakehouse / KQL |
| `demo` | Neither | `data/customdata` CSVs |

The same tool contract is served in all three cases, so an agent behaves
identically regardless of backend. `EDGEIQ_DEMO_DATA_PATH` overrides the demo
data location (default `data/customdata`).

---

## MCP SDK compatibility

The installed `mcp` package may be 1.x or 2.x, and 2.x renamed the server class
(`FastMCP` → `MCPServer`) and the tool accessor (`.fn` → `.func`).

`src/mcp_servers/compat.py` shims both. **Any new server must use
`build_server()`, `http_app()` and `tool_fn()` from compat — never the SDK class
directly.** Importing `FastMCP` directly works on one generation and fails on
the other.

---

## Using these servers elsewhere

The servers have no Edge IQ dependencies. To reuse one:

```bash
cp -r src/mcp_servers/ /your/project/
pip install -r src/mcp_servers/requirements.txt
python -m uvicorn mcp_servers.gateway:app --port 8080
```

Replace `datasource.py` to point at your own systems; the tool contracts stay
the same, so consuming agents need no changes.

---

## Adding a tool

1. Add the function to the relevant server with `@mcp.tool()`.
2. Type the signature — the schema is generated from it, and the model sees
   nothing else.
3. Write a docstring; it becomes the tool description the model reasons over.
4. If it writes, gate it on `approved` **and** `EDGEIQ_ALLOW_WRITES`.
5. Grant it in `registry.py` to the agents that need it — and only those.
6. Run `python tests/smoke_mcp.py`.

---

## Related

- [Agents and routing](./agents.md)
- [Architecture](./architecture.md)
- [Configuration reference](./configuration.md)
