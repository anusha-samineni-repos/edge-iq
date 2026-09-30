# Azure Deployment Record

Resources in Azure that Edge IQ currently uses. Last updated 30 Sep 2026 by Anusha Samineni.

## Subscription and resource group

| Item | Value |
|---|---|
| Subscription | Visual Studio Enterprise Subscription - DefaultUseIt |
| Subscription ID | `cf41a9eb-73ab-46fc-be7b-10567c0516d8` |
| Resource group | `rg-iot-edge-pm-dev` |
| Region | West US |

## Microsoft Foundry (existing resource, reused)

No new Foundry resource was created. The agents were registered into the existing project.

| Item | Value |
|---|---|
| Foundry resource (AIServices) | `aif-vwcdscvow6xdu` |
| Foundry project | `aif-vwcdscvow6xdu-proj` |
| Project endpoint | `https://aif-vwcdscvow6xdu.services.ai.azure.com/api/projects/aif-vwcdscvow6xdu-proj` |
| Model deployment | `gpt-4.1-mini` (version 2025-04-14) |

## Foundry agents (created by `infra/scripts/seed/register_agents.py`)

All agents carry the metadata tag `solution=edge-iq`.

| Agent | Role | Agent ID |
|---|---|---|
| **edgeiq-orchestrator** | **Main agent**: Solution Orchestrator, routes to specialists via connected agents | `asst_7dclv5bdyHNSIyAp0D8UBdZ3` |
| edgeiq-asset-health | UC1 asset health and predictive maintenance | `asst_Zd8sWzIZMgImkd2ntfFTAzG7` |
| edgeiq-water-quality | UC2 water quality and compliance | `asst_rLzMFg9ext5mL2yLs6I8oiyi` |
| edgeiq-leak-detection | UC3 leak detection and NRW | `asst_EUIPVgbUPUcsxrppsMAQOr3b` |
| edgeiq-energy-optimizer | UC4 pump energy optimisation | `asst_ihYD5X5WPM13Io0hy0n2tEVc` |
| edgeiq-fleet-operations | UC5 edge fleet operations and security | `asst_ykb1vVf3Cbb5pjUr7HkM28rC` |
| edgeiq-knowledge-navigator | SOPs, manuals and standards | `asst_OgbNAfwVza4hPNdTPZ9uUrHf` |
| edgeiq-maintenance-planner | Work orders (approval-gated) | `asst_OExeo14GHtISgqsTXRXIcqol` |

## Not yet provisioned

The following are defined in `infra/` but have **not** been deployed:

- Cosmos DB
- Container Apps (API and MCP gateway)
- AI Search
- Key Vault
- App Insights
- Fabric workspace, Lakehouse and Eventhouse
- Copilot Studio agent
- M365 (Work IQ) app registration

Until the MCP gateway is deployed, the Foundry specialists have **no MCP tools attached**. Foundry cannot reach `localhost`, so the agents answer from their instructions and model knowledge only. After `azd deploy` of the MCP app, re-register with the public URL:

```powershell
.\.venv\Scripts\python.exe infra\scripts\seed\register_agents.py `
  --project-endpoint "https://aif-vwcdscvow6xdu.services.ai.azure.com/api/projects/aif-vwcdscvow6xdu-proj" `
  --model gpt-4.1-mini --gateway-url "https://<mcp-gateway-fqdn>"
```

Re-running the script updates the agents in place. Run `--delete` to remove every `solution=edge-iq` agent.

## Run the main agent from a terminal

The main agent is **`edgeiq-orchestrator`**. Prerequisites: run `az login` (as the account with access to the subscription above), then `pip install azure-ai-agents azure-identity`.

```powershell
cd C:\IQAgent\edge-iq
.\.venv\Scripts\python.exe infra\scripts\ask_foundry_agent.py "WTP-01-PUMP-003 vibration p95 is 8.0 mm/s on a 75 kW pump. Which ISO 10816-3 zone is that, what should we do, and which specialist owns it?"
```

### Example test run (actual output)

```text
(.venv) PS C:\IQAgent\edge-iq> .\.venv\Scripts\python.exe infra\scripts\ask_foundry_agent.py "WTP-01-PUMP-003 vibration p95 is 8.0 mm/s on a 75 kW pump. Which ISO 10816-3 zone is that, what should we do, and which specialist owns it?"
Q: WTP-01-PUMP-003 vibration p95 is 8.0 mm/s on a 75 kW pump. Which ISO 10816-3 zone is that, what should we do, and which specialist owns it?
run: RunStatus.COMPLETED

The vibration p95 of 8.0 mm/s RMS velocity on the 75 kW pump WTP-01-PUMP-003 places it in ISO 10816-3 Zone C, which is an unsatisfactory condition indicating potential damage. Immediate detailed inspection and planning for an overhaul are recommended due to the high urgency, as continued operation risks catastrophic failure and water supply disruption. The asset-health specialist owns this issue and should lead the diagnosis and intervention planning.

What I checked: Asset Health IQ for vibration severity, ISO 10816-3 standards, failure mode and RUL estimates.
```

What this run confirms:

- The orchestrator run completes.
- It routes the question to the **asset-health** specialist.
- It returns a recommended action.

> **Known gap:** the zone in this answer is wrong. For a 75 kW machine (ISO 10816-3 group 2), the zone boundaries are 1.4, 2.8 and 4.5 mm/s on rigid foundations, or 2.3, 4.5 and 7.1 mm/s on flexible ones. Either way, 8.0 mm/s is **Zone D** (damage likely, stop the machine), not Zone C. The cloud agents do not have their MCP tools attached yet, because the MCP gateway only runs locally, so they answer from model knowledge. The local demo, which calls the MCP tools, answers Zone D correctly. To fix this, register the agents with `--gateway-url` pointing at the deployed MCP gateway (see [DEPLOY_TO_PRODUCTION.md](./DEPLOY_TO_PRODUCTION.md), step 6.3).

Override the target with the `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` and `AZURE_AI_FOUNDRY_ORCHESTRATOR_AGENT_ID` environment variables.

To use these cloud agents from the local web console instead of demo mode, set:

```powershell
$env:EDGEIQ_DEMO_MODE="false"
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://aif-vwcdscvow6xdu.services.ai.azure.com/api/projects/aif-vwcdscvow6xdu-proj"
$env:AZURE_AI_FOUNDRY_ORCHESTRATOR_AGENT_ID="asst_7dclv5bdyHNSIyAp0D8UBdZ3"
```
