# Agents and routing

Edge IQ uses one orchestrator and seven specialist agents. This document
describes each agent, what it can reach, and how questions get routed.

The roster is defined in `src/api/python/agents/registry.py`. It is the single
source of truth — the API, the Foundry registration script and the Copilot
Studio manifest all read from it.

---

## Roster

| Agent | Domain | MCP tools |
|---|---|---|
| `edgeiq-orchestrator` | Routing and synthesis | 7 connected agents |
| `asset-health` | Rotating asset condition, failure prediction, RUL | `edge-fleet`, `historian`, `asset-registry`, `work-order` |
| `water-quality` | Residuals, turbidity, compliance excursions | `water-quality`, `historian`, `asset-registry`, `work-order` |
| `leak-detection` | DMA night flow, bursts, non-revenue water | `historian`, `asset-registry`, `edge-fleet`, `work-order` |
| `energy-optimizer` | Pump efficiency, tariffs, carbon | `historian`, `asset-registry`, `edge-fleet` |
| `fleet-operations` | Device connectivity, twin drift, certificates | `edge-fleet`, `asset-registry`, `historian`, `work-order` |
| `knowledge-navigator` | Standards, SOPs, regulation | **none** |
| `maintenance-planner` | Work order drafting, scheduling | `work-order`, `asset-registry` |

Regenerate this table at any time:

```bash
python infra/scripts/seed/register_agents.py --dry-run
```

---

## Why `knowledge-navigator` has no tools

This is the design decision most worth understanding.

`knowledge-navigator` answers from governed knowledge only — Foundry IQ and
nothing else. It cannot query a device, read telemetry or touch a work order.

That restriction is deliberate. The agent whose job is *"read the procedure and
tell me what it says"* has no business reaching operational systems, and giving
it that reach creates a path from a document-shaped prompt injection to a device
interaction. Entitlements are per agent precisely so this one can be empty.

If `knowledge-navigator` needs data to answer, the router should have selected a
different agent — or both, in parallel.

---

## Routing

The router runs **after** the unified context is assembled, so it sees resolved
entities rather than raw text. `route()` returns a `RouteDecision`:

```json
{
  "strategy": "single",
  "agents": ["asset-health"],
  "confidence": 0.86,
  "rationale": "Vibration reading on a rotating asset with a resolved device ID",
  "entities": { "deviceIds": ["WTP-01-PUMP-003"], "metric": "vibration" }
}
```

All of it is returned to the client and rendered in the console.

### Strategies

| Strategy | Trigger | Behaviour |
|---|---|---|
| `single` | One clear domain | Direct to one specialist |
| `parallel` | Independent sub-questions | Fan out, merge |
| `handoff` | Sequential dependency | Output of one feeds the next |
| `magentic` | Open-ended or ambiguous | Planner decomposes dynamically |

**Examples**

- *"Is the RES-07 analyser faulty?"* → `single` → `water-quality`
- *"Which pumps lost efficiency and what did it cost?"* → `single` →
  `energy-optimizer`
- *"The pump is drawing more power and vibrating"* → `parallel` →
  `asset-health` + `energy-optimizer` (two independent readings of one symptom)
- *"Diagnose the DMA-14 problem and raise the work order"* → `handoff` →
  `leak-detection` → `maintenance-planner`
- *"What should I worry about this week?"* → `magentic` (no single domain; the
  planner decides what to check)

### Entity extraction

Two ID patterns drive most routing:

```
device   \b([A-Z]{2,4}-\d{1,3}-[A-Z0-9]{2,6}-\d{1,3})\b     WTP-01-PUMP-003
site     \b((?:WTP|WWTP|PS|RES|DMA|WELL)-\d{1,3})\b          RES-07, DMA-14
```

For every generated asset, `assetId == deviceId`.

Non-ID vocabulary resolves through the Fabric IQ ontology, which is why
*"chlorine"* reaches `water-quality` and *"pump"* reaches `asset-health` without
either word appearing in a routing rule.

### Preview a route without spending tokens

```bash
curl -X POST localhost:8000/api/route-preview \
  -H 'Content-Type: application/json' \
  -d '{"message":"Is there a leak in DMA-14?"}'
```

Returns the routing decision with no model invocation. Useful for demos and for
debugging why a question landed where it did.

---

## Orchestration modes

`EDGEIQ_ORCHESTRATION_MODE` selects how the orchestrator coordinates agents:

| Mode | Behaviour |
|---|---|
| `magentic` *(default)* | Magentic planner; dynamic decomposition, best for open-ended work |
| `handoff` | Explicit sequential chains |
| `single` | Always one agent — lowest latency, lowest cost |

Bounded by `EDGEIQ_MAX_ROUNDS` (default 12) and `EDGEIQ_MAX_STALL_ROUNDS`
(default 3). The stall bound matters: without it, two agents can pass a task
back and forth without converging, and the only symptom is a slow, expensive
turn.

---

## Writes and approval

Any tool that changes state requires **two independent keys**:

1. `approved=true` on the tool call
2. `EDGEIQ_ALLOW_WRITES=true` on the deployment

With neither or only one, the action is prepared and returned as a pending
approval with `committed: false`.

Two keys rather than one because they fail differently. A model can be talked
into setting `approved=true`; it cannot change a deployment environment
variable. An operator can arm the environment; they still see every action
before it commits.

```json
{
  "committed": false,
  "approvalRequired": true,
  "action": "create_work_order",
  "summary": "Bearing replacement, WTP-01-PUMP-003, next planned outage"
}
```

---

## Adding an agent

1. Add an `AgentSpec` to `SPECIALIST_SPECS` in `registry.py` — name,
   description, instructions, and the MCP servers it may use.
2. Add routing rules in `routing.py` so questions reach it.
3. Re-run `python infra/scripts/seed/register_agents.py` to register it with
   Foundry and refresh the Copilot Studio manifest.
4. Run `pytest` — `smoke_api.py` checks routing and `smoke_scenarios.py`
   validates that scenarios only reference agents that exist.

Grant the narrowest set of tools that lets the agent do its job. Entitlements
are easier to widen later than to claw back.

---

## Related

- [Architecture](./architecture.md)
- [MCP servers and tools](./mcp-servers.md)
- [IQ layers](./iq-layers.md)
- [Demo guide](./demo-guide.md)
