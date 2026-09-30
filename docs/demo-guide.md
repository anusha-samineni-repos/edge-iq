# Demo guide

Five scenarios, each demonstrating a different capability of the solution. They
run entirely on generated data — no Azure resources required.

Full detail lives in `data/scenarios/*.json`: seeded fault parameters, the
question script, what to expect, and what to watch for. This guide covers setup
and sequencing.

---

## Setup

```bash
python data/generators/generate_reference.py
python data/generators/generate_telemetry.py --days 30 --seed 7
```

```bash
# terminal 1
set PYTHONPATH=src
python -m uvicorn mcp_servers.gateway:app --port 8080

# terminal 2
set EDGEIQ_DEMO_MODE=true
set PYTHONPATH=src
python -m uvicorn api.python.app:app --port 8000
```

Optional console:

```bash
cd src/App && npm install && npm run dev
```

`--seed 7` is deterministic. The same seed produces the same faults, so a demo
reproduces exactly — worth pinning before presenting.

---

## The five scenarios

| | Scenario | Shows | Device |
|---|---|---|---|
| **UC1** | [Asset health](../data/scenarios/uc1-asset-health.json) | Standards-grounded diagnosis, gated writes | `WTP-01-PUMP-003` |
| **UC2** | [Water quality](../data/scenarios/uc2-water-quality.json) | Distinguishing a breach from a dead sensor | `RES-03-CL2-001` |
| **UC3** | [Leak detection](../data/scenarios/uc3-leak-detection.json) | Network analysis, NRW quantification | `DMA-14` |
| **UC4** | [Energy optimisation](../data/scenarios/uc4-energy-optimisation.json) | Efficiency degradation, tariff exposure | `PS-04-PUMP-001` |
| **UC5** | [Fleet operations](../data/scenarios/uc5-fleet-operations.json) | Estate health, certificates, twin drift | `WTP-01-GW-001` |

---

## Recommended order

**UC1 first.** It establishes the pattern — a question, a route, evidence, a
gated action — and everything after reads as a variation on it.

**UC2 second.** The strongest scenario, because it is the one where a naive
system fails. Two chlorine analysers both show invariant readings; one is a
genuine breach at the regulatory floor, the other is a flatlined instrument.
Dismissing the first as a sensor fault suppresses a public health event.
Escalating the second dispatches a crew to a working sensor.

The rule is `invariant AND breaches == 0` — and the second condition is the
whole point.

**UC3 / UC4 as time allows.** Network-scale and financial framing respectively.

**UC5 to close.** Certificate expiry and twin drift make the point that edge
estates fail silently — a gateway that stops reporting produces no alarm, only
absence.

---

## What to point at

Regardless of scenario:

**The route panel.** It names the agent chosen and why. Routing is visible
rather than implicit — when an answer looks wrong, the first question is whether
the right specialist saw it.

**The layer pills.** Work IQ reads `placeholder` in demo mode. That is the
designed behaviour, not a failure: layers degrade independently and say so.

**Citations.** Every claim traces to a source. An answer about a zone D pump
that cites no standard is pattern-matching.

**The approval panel.** UC1 step 4 drafts a work order without committing it.
Two independent keys are required — `approved=True` on the call *and*
`EDGEIQ_ALLOW_WRITES=true` on the deployment. They fail differently: a model can
be argued into the first and cannot touch the second.

---

## Changing the scenarios

Every fault parameter lives in the `SCENARIO` dict in
`data/generators/generate_telemetry.py` (lines 37–63):

```python
"uc1_peak_vibration": 7.9,     # ISO zone D at 75 kW
"uc2_min_chlorine":   0.14,    # below the 0.2 mg/L floor
"uc3_mnf_end":        19.2,    # m³/h night flow
```

Change a value, re-run the generator, and both the data and the demonstrated
behaviour move together.

Useful variation: set `uc1_peak_vibration` to `5.0`. On a 75 kW machine that is
zone B — acceptable. The same question now gets a "monitor, no action" answer,
which demonstrates that the threshold really is being read from the asset record
rather than baked into a prompt.

---

## Verifying before a demo

```bash
pytest
```

Six suites, ~3 seconds. Covers IQ layers, MCP servers, API routing, container
layout, console module graph, and scenario integrity — the last of which checks
that the scenario files still match the generator, the agent registry and the
generated CSVs.

---

## Related

- [Agents and routing](./agents.md)
- [MCP servers](./mcp-servers.md)
- [Deployment guide](./deployment.md)
