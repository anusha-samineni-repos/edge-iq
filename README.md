# Edge IQ — Water Utility Edge Intelligence

A multi-agent solution that answers operational questions about a water
utility's IoT Edge estate — grounded in live telemetry, engineering standards
and organisational context.

Built on the Microsoft IQ solution orchestrator pattern: three IQ layers
(**Work IQ**, **Fabric IQ**, **Foundry IQ**) feeding a **Solution Orchestrator**
that routes to specialist agents over MCP tools.

---

## Why this exists

Most edge analytics answers one question — *will this fail?* — and answers it in
isolation. An operator's actual question is broader:

> *Vibration on pump 3 is climbing. Is that bad? What does the standard say? Has
> anyone looked at it? Can we defer it to the planned outage?*

That needs telemetry, an engineering standard, organisational memory and a
maintenance system in one answer. Edge IQ composes them.

---

## Try it in five minutes

No Azure resources required.

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r src/api/python/requirements.txt
pip install -r src/mcp_servers/requirements.txt

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

Then ask:

```bash
curl -X POST localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"Vibration on WTP-01-PUMP-003 is climbing. What is the ISO zone?\"}"
```

Walkthrough: [docs/demo-guide.md](./docs/demo-guide.md)

### Example commands (PowerShell, from the repo root)

**Local web app with UX (demo mode)**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src\api\python\requirements.txt -r src\mcp_servers\requirements.txt

# terminal 1 - MCP gateway (5 MCP servers)
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m uvicorn mcp_servers.gateway:app --port 8080

# terminal 2 - build the UI once, then run the API (serves the UI at http://localhost:8000)
cd src\App; npm install; npm run build; cd ..\..
$env:PYTHONPATH = "src"; $env:EDGEIQ_DEMO_MODE = "true"
.\.venv\Scripts\python.exe -m uvicorn api.python.app:app --port 8000
```

**Call the API**

```powershell
Invoke-RestMethod http://localhost:8000/api/health
Invoke-RestMethod http://localhost:8000/api/agents

# Non-streaming chat
$body = @{ message = "Vibration on WTP-01-PUMP-003 is climbing. What is the ISO zone?"; stream = $false } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/chat -ContentType "application/json" -Body $body

# Which agents would handle a prompt (no execution)
$body = @{ message = "Give me the morning briefing for the whole estate" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/route-preview -ContentType "application/json" -Body $body

# Streaming (SSE) with curl
curl.exe -N -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d "{\"message\":\"Is there a leak in DMA-07?\",\"stream\":true}"
```

**Tests**

```powershell
$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m pytest
```

**Cloud agents in Microsoft Foundry** (see [AZURE_RESOURCES.md](./AZURE_RESOURCES.md))

```powershell
az login
# Ask the main agent (edgeiq-orchestrator)
.\.venv\Scripts\python.exe infra\scripts\ask_foundry_agent.py "Vibration on WTP-01-PUMP-003 is 6.2 mm/s. What ISO 10816 zone is it and what should we do?"
.\.venv\Scripts\python.exe infra\scripts\ask_foundry_agent.py "Which edge gateways have certificates expiring in the next 30 days?"
.\.venv\Scripts\python.exe infra\scripts\ask_foundry_agent.py "Chlorine residual at Reservoir R-02 dropped to 0.3 mg/L. Is that a compliance issue?"

# Register / update / remove the 8 agents
$ep = "https://aif-vwcdscvow6xdu.services.ai.azure.com/api/projects/aif-vwcdscvow6xdu-proj"
.\.venv\Scripts\python.exe infra\scripts\seed\register_agents.py --project-endpoint $ep --model gpt-4.1-mini --dry-run
.\.venv\Scripts\python.exe infra\scripts\seed\register_agents.py --project-endpoint $ep --model gpt-4.1-mini --gateway-url https://<mcp-gateway-fqdn>
.\.venv\Scripts\python.exe infra\scripts\seed\register_agents.py --project-endpoint $ep --delete
```

**Deploy to Azure**: full click-by-click guide in [DEPLOY_TO_PRODUCTION.md](./DEPLOY_TO_PRODUCTION.md).

---

## Architecture

```
    Teams / M365 Copilot          Operator console
              │                          │
        Copilot Studio                   │
              └───────────┬──────────────┘
                          ▼
            ┌──────────────────────────┐
            │  Solution Orchestrator   │
            └──────────────────────────┘
                          │
    ┌──────────┬──────────┼──────────┬──────────┐
    ▼          ▼          ▼          ▼          ▼
 asset-    water-      leak-     energy-     fleet-      + knowledge-navigator
 health    quality   detection  optimizer  operations    + maintenance-planner
    └──────────┴──────────┼──────────┴──────────┘
                          ▼
                    MCP gateway
    ┌──────────┬──────────┼──────────┬──────────┐
 edge-fleet historian asset-registry water-quality work-order
                          │
    ┌─────────────────────┼─────────────────────┐
    ▼                     ▼                     ▼
 Work IQ              Fabric IQ            Foundry IQ
 (M365 / Graph)   (OneLake / Lakehouse)  (AI Search / RAG)
```

Detail: [docs/architecture.md](./docs/architecture.md)

---

## Five use cases

| | Scenario | Capability |
|---|---|---|
| UC1 | Asset health | Standards-grounded diagnosis, gated work orders |
| UC2 | Water quality | Breach vs. flatlined analyser |
| UC3 | Leak detection | DMA night flow, non-revenue water |
| UC4 | Energy optimisation | Efficiency loss, tariff exposure |
| UC5 | Fleet operations | Certificates, twin drift, silent failure |

Each has a seeded fault and a question script in
[`data/scenarios/`](./data/scenarios/).

---

## What ships

| | |
|---|---|
| **Infrastructure** | Bicep for Cosmos, Fabric, Foundry, AI Search, Container Apps, Key Vault, App Insights |
| **Orchestrator** | Magentic / handoff / single modes, SSE streaming, conversation memory |
| **Agents** | 8 specifications with per-agent tool entitlements |
| **MCP servers** | 5 servers, 36 typed tools, standalone and reusable |
| **Knowledge base** | 25 documents — standards, SOPs, regulatory limits |
| **Fabric artifacts** | Medallion notebooks, eventstream, semantic ontology |
| **Copilot Studio** | Connector, declarative agent, 3 topics |
| **Console** | React operator UI with route, layer and citation panels |
| **Demo data** | Deterministic generators, 19 tables, 5 seeded faults |
| **Tests** | 6 smoke suites under pytest |

---

## Documentation

- [ARCHITECTURE_FLOW.md](ARCHITECTURE_FLOW.md): end-to-end flow of a question through every layer

| | |
|---|---|
| [Architecture](./docs/architecture.md) | Components and request flow |
| [IQ layers](./docs/iq-layers.md) | Work IQ, Fabric IQ, Foundry IQ in detail |
| [Agents and routing](./docs/agents.md) | The 8 agents and how routing works |
| [MCP servers](./docs/mcp-servers.md) | Tools, entitlements, write gating |
| [Data platform](./docs/data-platform.md) | Cosmos, Lakehouse, OneLake, ontology |
| [Deployment](./docs/deployment.md) | Local, dev and production paths |
| [Configuration](./docs/configuration.md) | Every environment variable |
| [Work IQ setup](./docs/work-iq-setup.md) | M365 app registration and OBO |
| [Copilot Studio setup](./docs/copilot-studio-setup.md) | Connector and channels |
| [Demo guide](./docs/demo-guide.md) | Running the scenarios |
| [Next steps](./next-steps.md) | Extending for your estate |

---

## Deploy to Azure

> **Current deployment:** agents are live in Foundry project `aif-vwcdscvow6xdu-proj` (resource group `rg-iot-edge-pm-dev`). See [AZURE_RESOURCES.md](AZURE_RESOURCES.md) for every resource, the agent IDs, and the terminal command to run the main agent (`edgeiq-orchestrator`).

```bash
azd auth login
azd env new edgeiq-dev
azd up
.\infra\scripts\seed\seed-all.ps1
```

[Deployment guide](./docs/deployment.md)

---

## Design decisions worth knowing

**Every layer degrades independently.** Missing configuration produces a
`placeholder` status and a working answer from the remaining layers — not an
error, and not a silent omission.

**Writes need two keys.** `approved=True` on the call *and*
`EDGEIQ_ALLOW_WRITES=true` on the deployment. They fail differently: a model can
be argued into the first and cannot reach the second.

**Governance is a query-time filter.** Above-ceiling content is never retrieved,
rather than retrieved and scrubbed.

**Thresholds come from the asset record.** ISO 10816-3 bands depend on rated
power. An agent assuming a fixed threshold is wrong about half the estate.

**Directory depth is load-bearing.** Modules resolve the repo root by walking up
from their own file; the container images preserve `src/` depth for that reason.
`tests/smoke_containers.py` guards it.

---

## Tests

```bash
pytest
```

---

## License

MIT — see [LICENSE](./LICENSE).
