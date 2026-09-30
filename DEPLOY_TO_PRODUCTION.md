# Deploy Edge IQ to Production: Step-by-Step Guide

**Author:** Anusha Samineni (anusha.samineni@outlook.com)

This guide walks through every command and portal click needed to take Edge IQ from this repository to a production Azure environment:

- Solution Orchestrator plus 7 specialist agents in Microsoft Foundry
- 5 MCP servers
- Cosmos DB
- AI Search (Foundry IQ)
- Fabric Lakehouse, OneLake and Eventhouse (Fabric IQ)
- M365 Graph (Work IQ)
- Copilot Studio in Teams

All commands are **PowerShell 7 on Windows**, run from the repository root, unless a step says otherwise. Replace `<...>` placeholders with your own values.

> Related docs: [ARCHITECTURE_FLOW.md](./ARCHITECTURE_FLOW.md), [AZURE_RESOURCES.md](./AZURE_RESOURCES.md), [docs/deployment.md](./docs/deployment.md), [docs/configuration.md](./docs/configuration.md), [docs/work-iq-setup.md](./docs/work-iq-setup.md), [docs/copilot-studio-setup.md](./docs/copilot-studio-setup.md)

---

## 0. Overview of what gets deployed

| Layer | Azure / M365 resource | Created by |
|---|---|---|
| Hosting | Container Apps environment + 3 apps (`api`, `web`, `edgeiq-mcp-gateway`), Azure Container Registry | `azd up` (Bicep) |
| Foundry IQ | Foundry (AI Services) account + project, `gpt-4o` and `text-embedding-3-large` deployments, AI Search | `azd up` |
| Operational data | Cosmos DB (`edgeiq` DB: assets, alarms, workorders, compliance, telemetry_summary, agent_memory, agent_traces) | `azd up` + `seed_cosmos.py` |
| Edge ingestion | IoT Hub, Event Hubs (`edge-telemetry`) | `azd up` |
| Fabric IQ | Fabric capacity (F2+); workspace, Lakehouse, Eventhouse, notebooks, Eventstream | `azd up` (capacity) + portal / `seed_fabric.py` |
| Security & ops | User-assigned managed identity, Key Vault, App Configuration, Log Analytics, Application Insights, Storage | `azd up` |
| Agents | 8 Foundry agents (orchestrator + 7 specialists) with MCP tools | `register_agents.py` |
| Work IQ | Entra app registration with Graph permissions (SharePoint, Teams, Mail, Calendar) | Portal (Step 9) |
| Channel | Copilot Studio agent + custom connector, published to Teams / M365 Copilot | Portal (Step 10) |

Estimated time: about 2–3 hours the first time, including approvals.

---

## 1. Prerequisites (check every item)

### 1.1 Accounts, licenses and roles

| # | Requirement | Why | How to check |
|---|---|---|---|
| 1 | Azure subscription (production, not a Visual Studio / trial one) | Hosts all resources | Portal → **Subscriptions** |
| 2 | **Owner** on the subscription or target resource group, *or* **Contributor** + **User Access Administrator** | Bicep creates role assignments for the managed identity | Portal → Subscription → **Access control (IAM)** → **Check access** |
| 3 | Entra ID role **Application Administrator** or **Cloud Application Administrator** | Create the Work IQ app registration | Entra admin center → **Roles & admins** |
| 4 | Entra ID **Global Administrator** or **Privileged Role Administrator** (can be another person) | Grant admin consent for Graph application permissions | Same as above |
| 5 | Microsoft Fabric enabled in the tenant; you are a **Fabric Administrator** or a capacity admin | Create the workspace, Lakehouse and Eventhouse on the F-SKU capacity | app.fabric.microsoft.com → ⚙ → **Admin portal** |
| 6 | Power Platform environment (Production type) with Dataverse; **Environment Maker** or **System Administrator** role | Import the Copilot Studio agent and connector | admin.powerplatform.microsoft.com → **Environments** |
| 7 | **Copilot Studio** license (tenant capacity or pay-as-you-go billing plan linked to the environment) | Publish the agent | Power Platform admin center → **Billing** → **Licenses** |
| 8 | **Microsoft 365 E3/E5** (or Business Premium) users with Teams and SharePoint | Work IQ sources and the Teams channel | M365 admin center → **Billing** → **Your products** |
| 9 | Teams admin: permission to allow custom apps (**Teams Administrator**) | Publish the agent to Teams | Teams admin center → **Teams apps** → **Setup policies** |
| 10 | GitHub access to `anusha-samineni-repos/edge-iq` | Clone the source | `git ls-remote https://github.com/anusha-samineni-repos/edge-iq` |

### 1.2 Quotas and regions

| Item | Minimum | How to check / request |
|---|---|---|
| Azure OpenAI `gpt-4o` GlobalStandard | 50K TPM (`AZURE_OPENAI_CHAT_CAPACITY=50`) | Foundry portal (ai.azure.com) → **Management center** → **Quota**, or `az cognitiveservices usage list -l <region> -o table` |
| Azure OpenAI `text-embedding-3-large` Standard | 50K TPM | Same |
| Fabric capacity | F2 for pilots, **F8 or larger** recommended for production | Portal → **Quotas** → *Microsoft Fabric* |
| Container Apps | 1 environment, about 6 vCPU | Portal → **Quotas** → *Container Apps* |
| AI Search | Basic for pilots, **Standard S1** for production (semantic ranker) | Pricing tier at deploy time |
| Region | Must support gpt-4o GlobalStandard, AI Search semantic ranker, Fabric and Container Apps. Recommended: **eastus2**, **swedencentral**, **westus3** | [Model availability](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/models) |

### 1.3 Resource providers (register once per subscription)

```powershell
$providers = "Microsoft.App","Microsoft.ContainerRegistry","Microsoft.CognitiveServices","Microsoft.Search",
  "Microsoft.DocumentDB","Microsoft.Devices","Microsoft.EventHub","Microsoft.Fabric","Microsoft.KeyVault",
  "Microsoft.AppConfiguration","Microsoft.OperationalInsights","Microsoft.Insights","Microsoft.Storage",
  "Microsoft.ManagedIdentity"
$providers | Sort-Object -Unique | ForEach-Object { az provider register --namespace $_ }
$providers | Sort-Object -Unique | ForEach-Object { "{0,-35} {1}" -f $_, (az provider show -n $_ --query registrationState -o tsv) }
```

Wait until every provider shows `Registered`, which can take up to 10 minutes.

### 1.4 Workstation tools

| Tool | Version | Install (winget) | Verify |
|---|---|---|---|
| PowerShell | 7.4+ | `winget install Microsoft.PowerShell` | `$PSVersionTable.PSVersion` |
| Git | 2.40+ | `winget install Git.Git` | `git --version` |
| Azure CLI | 2.60+ | `winget install Microsoft.AzureCLI` | `az version` |
| Azure Developer CLI (azd) | **1.15.0+** | `winget install Microsoft.Azd` | `azd version` |
| Bicep | 0.30+ | `az bicep install` | `az bicep version` |
| Python | **3.12** (3.11 minimum) | `winget install Python.Python.3.12` | `python --version` |
| Node.js | **20 LTS+** (tested on 24) | `winget install OpenJS.NodeJS.LTS` | `node -v; npm -v` |
| Docker Desktop | Optional. `az acr build` builds in the cloud | `winget install Docker.DockerDesktop` | `docker version` |
| Azure CLI extensions | latest | `az extension add -n containerapp --upgrade; az extension add -n azure-iot --upgrade` | `az extension list -o table` |
| Power Platform CLI | Optional, for scripted solution import | `winget install Microsoft.PowerAppsCLI` | `pac help` |

Close and reopen the terminal after installing, so that `PATH` picks up the new tools.

### 1.5 Information to collect before you start

| Value | Where to find it | Used for |
|---|---|---|
| Subscription ID | `az account show --query id -o tsv` | `azd env` |
| Tenant ID | `az account show --query tenantId -o tsv` | `M365_TENANT_ID` |
| Your object ID | `az ad signed-in-user show --query id -o tsv` | `AZURE_PRINCIPAL_ID` |
| Fabric admin UPNs | e.g. `ops-admin@contoso.com` | `FABRIC_ADMIN_MEMBERS` |
| SharePoint site URLs holding SOPs and manuals | SharePoint → site → copy URL | `M365_SHAREPOINT_SITE_URLS` |
| Teams channel IDs for ops chatter | Teams → channel → **⋯** → **Get link to channel** (the `19:...@thread.tacv2` part) | `M365_TEAMS_CHANNEL_IDS` |
| Power Platform environment ID | Power Platform admin center → Environments → *env* → **Environment ID** | `COPILOT_STUDIO_ENVIRONMENT_ID` |
| (Optional) Existing Foundry project resource ID | Portal → Foundry project → **JSON View** → Resource ID | `AZURE_EXISTING_AI_PROJECT_RESOURCE_ID` |

---

## 2. Get the code and prepare the local environment

```powershell
git clone https://github.com/anusha-samineni-repos/edge-iq.git
cd edge-iq

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r src\api\python\requirements.txt -r src\mcp_servers\requirements.txt

# Sanity check: tests must pass before deploying
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest
```

Expected result: all smoke suites pass.

---

## 3. Sign in

```powershell
az login --tenant <tenant-id>
az account set --subscription <subscription-id>
az account show -o table          # confirm the right subscription

azd auth login --tenant-id <tenant-id>
```

---

## 4. Create and configure the production azd environment

```powershell
azd env new edgeiq-prod
azd env select edgeiq-prod
```

### 4.1 Core settings

```powershell
azd env set AZURE_SUBSCRIPTION_ID   <subscription-id>
azd env set AZURE_LOCATION          eastus2
azd env set AZURE_SOLUTION_NAME     edgeiq
azd env set AZURE_ENV_TYPE          prod
azd env set AZURE_PRINCIPAL_ID      (az ad signed-in-user show --query id -o tsv)
```

### 4.2 Models (Foundry IQ)

```powershell
azd env set AZURE_OPENAI_CHAT_DEPLOYMENT      gpt-4o
azd env set AZURE_OPENAI_CHAT_VERSION         2024-11-20
azd env set AZURE_OPENAI_CHAT_CAPACITY        100
azd env set AZURE_OPENAI_EMBEDDING_DEPLOYMENT text-embedding-3-large
azd env set AZURE_OPENAI_EMBEDDING_VERSION    1
azd env set AZURE_OPENAI_EMBEDDING_CAPACITY   50
```

**Optional: reuse an existing Foundry project** instead of creating a new one (for example, the dev project `aif-vwcdscvow6xdu-proj`):

```powershell
azd env set AZURE_EXISTING_AI_PROJECT_RESOURCE_ID "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>"
```

> When you reuse a project, the Bicep output `AZURE_AI_AGENT_ENDPOINT` is empty. Set it yourself after provisioning (Step 6.1).

### 4.3 Data, compute and scale

```powershell
azd env set COSMOS_SERVERLESS false        # provisioned throughput for production SLAs
azd env set COSMOS_THROUGHPUT 4000         # autoscale max RU/s
azd env set SEARCH_SKU        standard
azd env set IOTHUB_SKU        S1
azd env set IOTHUB_CAPACITY   2
azd env set EVENTHUB_CAPACITY 2
azd env set MIN_REPLICAS      1            # avoid cold starts in production
azd env set MAX_REPLICAS      10
azd env set IMAGE_TAG         (git rev-parse --short HEAD)
```

### 4.4 Fabric IQ

```powershell
azd env set DEPLOY_FABRIC_CAPACITY true
azd env set FABRIC_CAPACITY_SKU    F8
azd env set FABRIC_ADMIN_MEMBERS   '["ops-admin@contoso.com","anusha.samineni@outlook.com"]'
azd env set FABRIC_WORKSPACE_NAME  EdgeIQ-WaterUtility
azd env set FABRIC_LAKEHOUSE_NAME  EdgeIQ_Lakehouse
azd env set FABRIC_EVENTHOUSE_NAME EdgeIQ_Telemetry
```

If you already have a Fabric capacity, set `DEPLOY_FABRIC_CAPACITY false` and assign the workspace to that capacity in Step 8.

### 4.5 Work IQ (M365) and Copilot Studio placeholders

The client ID is filled in after Step 9. Placeholders are fine for now.

```powershell
azd env set M365_TENANT_ID                  <tenant-id>
azd env set M365_CLIENT_ID                  <set-after-step-9>
azd env set M365_SHAREPOINT_SITE_URLS       '["https://contoso.sharepoint.com/sites/WaterOps"]'
azd env set M365_TEAMS_CHANNEL_IDS          '["19:abc123@thread.tacv2"]'
azd env set COPILOT_STUDIO_ENVIRONMENT_ID   <power-platform-environment-id>
azd env set COPILOT_STUDIO_AGENT_SCHEMA_NAME edgeiq_water_edge_agent
```

Review all values:

```powershell
azd env get-values
```

Parameter reference: [infra/main.parameters.json](./infra/main.parameters.json) and [docs/configuration.md](./docs/configuration.md).

---

## 5. Provision and deploy (azd up)

```powershell
azd up
```

What happens:

1. **Package**: builds `api` (ApiApp.Dockerfile) and `web` (WebApp.Dockerfile, the React SPA).
2. **Provision**: deploys [infra/main.bicep](./infra/main.bicep) into `rg-edgeiq-prod` (about 15–25 minutes).
3. **Deploy**: pushes images to ACR and updates the Container Apps.
4. **Post-provision hook**: prints the seeding steps.

If you prefer separate steps:

```powershell
azd provision
azd deploy
```

Save the outputs:

```powershell
azd env get-values > .azure\edgeiq-prod-values.txt
azd env get-values | Select-String "WEB_APP_URL|API_APP_URL|MCP_GATEWAY_URL|AZURE_AI_AGENT_ENDPOINT|AZURE_CONTAINER_REGISTRY_NAME|AZURE_RESOURCE_GROUP"
```

### 5.1 Build and push the MCP gateway image

The MCP gateway is a container app created by Bicep with a placeholder image. It is not an azd service, so build and push its image separately:

```powershell
$env:AZURE_CONTAINER_REGISTRY_NAME = (azd env get-value AZURE_CONTAINER_REGISTRY_NAME)
$tag = (azd env get-value IMAGE_TAG)
.\infra\scripts\build\build-and-push-acr.ps1 -Service mcp -Tag $tag

$rg  = (azd env get-value AZURE_RESOURCE_GROUP)
$acr = (azd env get-value AZURE_CONTAINER_REGISTRY_ENDPOINT)
az containerapp update -g $rg -n edgeiq-mcp-gateway --image "$acr/edgeiq/mcp-gateway:$tag"
```

Verify the gateway:

```powershell
$mcp = (azd env get-value MCP_GATEWAY_URL)
Invoke-RestMethod "$mcp/health"
```

**Portal check:** Azure portal → **Resource groups** → `rg-edgeiq-prod` → confirm the Container Apps environment, 3 container apps, Cosmos DB, AI Search, Foundry, IoT Hub, Event Hubs, Key Vault, App Configuration, Application Insights and Fabric capacity. All should show a *Succeeded* provisioning state.

---

## 6. Seed data and register the agents

### 6.1 (Only when reusing a Foundry project) set the agent endpoint

```powershell
azd env set AZURE_AI_AGENT_ENDPOINT "https://<account>.services.ai.azure.com/api/projects/<project>"
```

### 6.2 Run the seeder

This generates reference and telemetry data, then seeds Cosmos DB, the AI Search index, the Fabric staging tables and the Foundry agents, and generates the Copilot Studio connector.

```powershell
.\infra\scripts\seed\seed-all.ps1 -DryRun      # preview; nothing is written
.\infra\scripts\seed\seed-all.ps1 -Days 30     # real run
# add -SkipFabric if the Fabric workspace is not created yet (Step 8), then rerun later
```

> **Production data:** the generators create *demo* data. For production, skip `-SkipGenerate` only for the initial smoke test. Afterwards, load real assets, work orders and compliance records from your CMMS/SCADA historian into Cosmos DB and OneLake. See [docs/data-platform.md](./docs/data-platform.md).

### 6.3 Register the 8 agents with MCP tools attached

```powershell
$ep  = (azd env get-value AZURE_AI_AGENT_ENDPOINT)
$mcp = (azd env get-value MCP_GATEWAY_URL)
$model = (azd env get-value AZURE_OPENAI_CHAT_DEPLOYMENT)

.\.venv\Scripts\python.exe infra\scripts\seed\register_agents.py --project-endpoint $ep --model $model --gateway-url $mcp --dry-run
.\.venv\Scripts\python.exe infra\scripts\seed\register_agents.py --project-endpoint $ep --model $model --gateway-url $mcp
```

The script:

- is idempotent;
- creates the 7 specialists first, then the `edgeiq-orchestrator` with connected-agent tools;
- prints each agent ID.

Copy the **orchestrator** ID (`asst_...`).

**Portal check:** open https://ai.azure.com → select the project → **Agents**. You should see 8 agents. Open `edgeiq-orchestrator` → **Tools** and confirm the 7 connected agents are listed. Open a specialist and confirm its MCP tool points to `https://edgeiq-mcp-gateway...`.

### 6.4 Switch the API from demo mode to the cloud agents

```powershell
$rg = (azd env get-value AZURE_RESOURCE_GROUP)
$api = "edgeiq-api"
az containerapp update -g $rg -n $api --set-env-vars `
  EDGEIQ_DEMO_MODE=false `
  AZURE_AI_FOUNDRY_ORCHESTRATOR_AGENT_ID=<orchestrator-asst-id> `
  EDGEIQ_ALLOW_WRITES=false `
  EDGEIQ_MAX_CLASSIFICATION=internal
```

Keep `EDGEIQ_ALLOW_WRITES=false` until the approval flow has been tested (Step 12). After that, set it to `true` so that approved work orders are written back.

### 6.5 Grant the managed identity access to the Foundry project (reused project only)

When the project was created by Bicep, the role assignments already exist. When you reused an existing project, add them yourself:

```powershell
$mi = (azd env get-value AZURE_CLIENT_ID)
$miObj = (az ad sp show --id $mi --query id -o tsv)
$scope = "<foundry-account-resource-id>"
az role assignment create --assignee-object-id $miObj --assignee-principal-type ServicePrincipal --role "Azure AI Developer" --scope $scope
az role assignment create --assignee-object-id $miObj --assignee-principal-type ServicePrincipal --role "Cognitive Services OpenAI User" --scope $scope
```

---

## 7. Foundry IQ knowledge index (AI Search)

`seed-all.ps1` already built the `edgeiq-knowledge` index from [knowledge/](./knowledge). To verify it:

1. Portal → AI Search service → **Indexes** → `edgeiq-knowledge`, and check the **Document count** is greater than 0.
2. Open **Search explorer**, query `ISO 10816 vibration zones`, and confirm the results come from `knowledge/`.
3. **Semantic ranker** → set to *Standard* (production).

To re-index after editing the knowledge base:

```powershell
.\.venv\Scripts\python.exe infra\scripts\seed\seed_search_index.py
```

---

## 8. Fabric IQ: workspace, Lakehouse, OneLake, Eventhouse, Eventstream

### 8.1 Workspace (clicks)

1. Go to https://app.fabric.microsoft.com → **Workspaces** → **+ New workspace**.
2. Name: `EdgeIQ-WaterUtility`. Expand **Advanced** → **License mode** → **Fabric capacity** → select the capacity from `azd env get-value FABRIC_CAPACITY_NAME`. Click **Apply**.
3. Workspace → **Manage access** → **+ Add people or groups**:
   - add the API managed identity (search its name `id-edgeiq-...`) as **Contributor**;
   - add the ops team as **Viewer**.
   - If service principals are not listed, enable them first: Admin portal → **Tenant settings** → **Developer settings** → *Service principals can use Fabric APIs* → **Enabled**.

### 8.2 Lakehouse (OneLake)

1. In the workspace, click **+ New item** → **Lakehouse** → name `EdgeIQ_Lakehouse` → **Create**.
2. Upload the notebooks. Go to **+ New item** → **Import notebook** → **Upload**, and select:
   - [fabric/notebooks/00_seed_reference_tables.py](./fabric/notebooks/00_seed_reference_tables.py)
   - [01_bronze_to_silver.py](./fabric/notebooks/01_bronze_to_silver.py)
   - [02_silver_to_gold.py](./fabric/notebooks/02_silver_to_gold.py)
3. Open each notebook → **Lakehouse** pane → **Add** → *Existing lakehouse* → `EdgeIQ_Lakehouse`.
4. Run `00` → `01` → `02` in order. Confirm the Delta tables appear under **Tables** (bronze, silver and gold).
5. Schedule `01` and `02`: notebook → **Run** → **Schedule** → every 15 minutes.

Stage the files from the CLI (uploads to OneLake):

```powershell
azd env set FABRIC_WORKSPACE_ID <workspace-guid-from-url>
azd env set FABRIC_LAKEHOUSE_ID <lakehouse-guid-from-url>
.\.venv\Scripts\python.exe infra\scripts\seed\seed_fabric.py --mode upload
```

### 8.3 Eventhouse (real-time telemetry)

1. Go to **+ New item** → **Eventhouse** → name `EdgeIQ_Telemetry` → **Create**. This also creates a KQL database with the same name.
2. Open the KQL database → copy the **Query URI**, then run:
   ```powershell
   azd env set FABRIC_KQL_ENDPOINT <query-uri>
   ```

### 8.4 Eventstream (IoT Hub → Eventhouse + Lakehouse)

1. Go to **+ New item** → **Eventstream** → name `edgeiq-telemetry-eventstream`.
2. **Add source** → **Azure IoT Hub**:
   - Connection: select the IoT Hub from `azd env get-value AZURE_IOTHUB_NAME`.
   - Shared access key name: `service`.
   - Consumer group: `edgeiq-agents`.
   - Data format: **JSON**.
3. **Add destination** → **Eventhouse** → `EdgeIQ_Telemetry` → table `telemetry` (create new) → **Direct ingestion**.
4. **Add destination** → **Lakehouse** → `EdgeIQ_Lakehouse` → table `bronze_telemetry`.
5. Click **Publish**.
6. Reference definition: [fabric/eventstream/edgeiq-telemetry-eventstream.json](./fabric/eventstream/edgeiq-telemetry-eventstream.json).

### 8.5 (Optional) Fabric Data Agent and ontology

Go to **+ New item** → **Data agent** → add `EdgeIQ_Lakehouse` and `EdgeIQ_Telemetry` as sources → paste the instructions from [fabric/ontology/water_utility_ontology.yaml](./fabric/ontology/water_utility_ontology.yaml) → **Publish**. Then copy its endpoint:

```powershell
azd env set FABRIC_DATA_AGENT_URL <data-agent-endpoint>
```

### 8.6 Push the Fabric settings to the API

```powershell
az containerapp update -g $rg -n $api --set-env-vars `
  FABRIC_WORKSPACE_ID=(azd env get-value FABRIC_WORKSPACE_ID) `
  FABRIC_LAKEHOUSE_ID=(azd env get-value FABRIC_LAKEHOUSE_ID) `
  FABRIC_KQL_ENDPOINT=(azd env get-value FABRIC_KQL_ENDPOINT) `
  FABRIC_KQL_DATABASE=EdgeIQ_Telemetry
```

---

## 9. Work IQ: Entra app registration for Microsoft 365 Graph

### 9.1 Register the app (clicks)

1. Go to https://entra.microsoft.com → **Identity** → **Applications** → **App registrations** → **+ New registration**.
2. Name: `EdgeIQ-WorkIQ`. Supported account types: **Accounts in this organizational directory only**. Click **Register**.
3. Copy the **Application (client) ID** and **Directory (tenant) ID**.
4. Go to **API permissions** → **+ Add a permission** → **Microsoft Graph** → **Application permissions**, and add:
   - `Sites.Read.All` (SharePoint SOPs and manuals)
   - `Files.Read.All`
   - `ChannelMessage.Read.All` (Teams ops channels)
   - `Calendars.Read` (shift and maintenance calendars; optional)
   - `ExternalItem.Read.All` (Graph connector content; optional)
5. Click **Grant admin consent for <tenant>** → **Yes**. Every permission should show a green check.
6. Choose how the app authenticates:
   - **Recommended:** use a federated credential for the managed identity. Go to **Certificates & secrets** → **Federated credentials** → **+ Add credential** → scenario *Managed identity* → select `id-edgeiq-...`.
   - **Alternative:** go to **Certificates & secrets** → **+ New client secret** → expiry 12 months, and copy the value into Key Vault:
     ```powershell
     $kv = ((azd env get-value AZURE_KEY_VAULT_ENDPOINT) -replace 'https://|\.vault\.azure\.net/?','')
     az keyvault secret set --vault-name $kv --name m365-client-secret --value "<secret>"
     ```
7. (Recommended) Limit SharePoint access to specific sites with `Sites.Selected` instead of `Sites.Read.All`. See [docs/work-iq-setup.md](./docs/work-iq-setup.md).

### 9.2 Apply the settings

```powershell
azd env set M365_CLIENT_ID <application-client-id>
azd provision                       # pushes the new values into App Configuration
az containerapp update -g $rg -n $api --set-env-vars M365_TENANT_ID=<tenant-id> M365_CLIENT_ID=<application-client-id>
```

---

## 10. Copilot Studio agent, published to Teams and M365 Copilot

### 10.1 Custom connector

`seed-all.ps1` generated the connector definition under [copilot-studio/generated/](./copilot-studio/generated), pointing at `API_APP_URL`. To regenerate it:

```powershell
.\.venv\Scripts\python.exe infra\scripts\seed\generate_connector.py
```

**Clicks:**

1. Go to https://make.powerapps.com → select the production environment (top right).
2. **More** → **Discover all** → **Custom connectors** → **+ New custom connector** → **Import an OpenAPI file**.
3. Name: `EdgeIQ API`. File: `copilot-studio/generated/*.swagger.json` (or the OpenAPI file in [copilot-studio/connectors/](./copilot-studio/connectors)). Click **Continue**.
4. **General** tab: confirm that the Host is the API FQDN, without `https://`.
5. **Security** tab: choose **API key** (header `x-edgeiq-key`) or **OAuth 2.0** with Entra ID, if Easy Auth is enabled (Step 11.1).
6. Click **Create connector**, then open the **Test** tab → **+ New connection** → run `chat` with `{"message":"health check"}` and expect a 200 response.

### 10.2 Agent (clicks)

1. Go to https://copilotstudio.microsoft.com → select the environment → **Create** → **New agent** → **Skip to configure**.
2. Fill in the agent details:
   - Name: `Edge IQ – Water Utility`.
   - Schema name: `edgeiq_water_edge_agent`.
   - Instructions: paste them from [copilot-studio/declarative-agent.json](./copilot-studio/declarative-agent.json).
3. **Knowledge** → **+ Add knowledge** → **SharePoint** → add the same site URLs used for Work IQ.
4. **Actions / Tools** → **+ Add a tool** → **Connector** → `EdgeIQ API` → `chat`. Map the input `message` to `Activity.Text`.
5. **Topics** → import or recreate the topics from [copilot-studio/topics/](./copilot-studio/topics): Asset health, Water quality, Leak, Energy, Fleet, Work order approval.
6. **Settings** → **Security** → **Authentication** → *Authenticate with Microsoft* (Entra ID).
7. Use **Test** to try the prompts from the README, then click **Publish**.
8. **Channels** → **Microsoft Teams and Microsoft 365 Copilot** → **Add channel** → **Availability options**:
   - **Show to my teammates**, for a pilot group; or
   - **Show to everyone in my org**, which sends it to Teams admin approval.
9. Teams admin: go to https://admin.teams.microsoft.com → **Teams apps** → **Manage apps** → find `Edge IQ – Water Utility` → **Publish** / **Allow**. Optionally pin it via **Setup policies**.

Full reference: [docs/copilot-studio-setup.md](./docs/copilot-studio-setup.md).

---

## 11. Production hardening

### 11.1 Authentication on the web and API apps (Easy Auth)

1. In the portal, go to the web container app → **Authentication** → **Add identity provider** → **Microsoft**.
2. App registration type: **Create new** (`EdgeIQ-Web`). Supported accounts: **Current tenant**.
3. Restrict access: **Require authentication**. Unauthenticated requests: **HTTP 302 redirect**. Click **Add**.
4. Repeat for the API app, but set *Unauthenticated requests* to **HTTP 401**. Then allow the Copilot Studio connector's client ID under **Allowed client applications**.

### 11.2 Network

- Container Apps environment: use a VNet-integrated environment, with ingress set to *internal* for the MCP gateway.
- Cosmos DB, AI Search, Key Vault, Storage and Foundry: go to **Networking** → **Private endpoint connections** → **+ Private endpoint**, then set **Public network access** to *Disabled*.
- IoT Hub: configure an IP filter or private endpoint for the edge gateways.

### 11.3 Secrets, identity and data

- All services use the user-assigned managed identity. Keep `disableLocalAuth` enabled on Cosmos DB and Foundry.
- Store any remaining secrets (Copilot Direct Line secret, inbound API key, M365 secret) in Key Vault, under the names `copilot-directline-secret`, `copilot-inbound-key` and `m365-client-secret`.
- Cosmos DB: go to **Backup & Restore** → *Continuous (7 days / 30 days)*.
- Use `EDGEIQ_MAX_CLASSIFICATION` to cap which data classifications the agents can return.

### 11.4 Monitoring

- Application Insights → **Transaction search**: confirm there are traces for `/api/chat`.
- Create alerts under **Alerts** → **+ Create** → **Alert rule**:
  - 5xx responses > 5 in 5 minutes on the API app;
  - Foundry `TokenTransaction` above 80% of quota;
  - Cosmos DB 429 throttling > 0.
- Foundry portal → **Tracing**: connect Application Insights to see per-agent run traces.

### 11.5 Scale and cost

- Set `MIN_REPLICAS=1` on the API and MCP gateway; the web app can stay at 0.
- Pause the Fabric capacity outside business hours if real-time ingestion is not needed: Portal → Fabric capacity → **Pause**.

---

## 12. Verify the production deployment

```powershell
$apiUrl = (azd env get-value API_APP_URL)
$webUrl = (azd env get-value WEB_APP_URL)

Invoke-RestMethod "$apiUrl/api/health"
Invoke-RestMethod "$apiUrl/api/version"
Invoke-RestMethod "$apiUrl/api/agents"

$q = @{ message = "Vibration on WTP-01-PUMP-003 is climbing. What is the ISO zone?"; stream = $false } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$apiUrl/api/chat" -ContentType "application/json" -Body $q |
  Select-Object answer, agentsUsed, layerStatus

# Direct test of the Foundry orchestrator agent
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = (azd env get-value AZURE_AI_AGENT_ENDPOINT)
$env:AZURE_AI_FOUNDRY_ORCHESTRATOR_AGENT_ID = "<orchestrator-asst-id>"
.\.venv\Scripts\python.exe infra\scripts\ask_foundry_agent.py "Give me the morning briefing for the whole estate"

# End-to-end smoke test against the deployed API
$env:EDGEIQ_API_URL = $apiUrl
.\.venv\Scripts\python.exe tests\smoke_api.py

Start-Process $webUrl
```

Acceptance checklist:

- [ ] `/api/health` returns `ok`, and `layerStatus` shows **live** (not *demo*) for Foundry IQ, Fabric IQ and Work IQ.
- [ ] The vibration prompt routes to `asset-health` and cites the ISO 10816 knowledge.
- [ ] The estate briefing fans out to 5 agents.
- [ ] "Raise a work order for PUMP-003" returns a **pending approval**. After approving in the UI, the work order appears in the Cosmos DB `workorders` container. This requires `EDGEIQ_ALLOW_WRITES=true`.
- [ ] Out-of-scope prompts ("what's the weather in Paris") are politely declined.
- [ ] The Copilot Studio agent answers the same prompts in Teams.
- [ ] Application Insights shows the traces, and the Foundry portal **Threads** shows the orchestrator runs.

---

## 13. Updates and CI/CD

```powershell
git pull
azd env select edgeiq-prod
azd env set IMAGE_TAG (git rev-parse --short HEAD)
azd deploy                                           # api + web
.\infra\scripts\build\build-and-push-acr.ps1 -Service mcp -Tag (azd env get-value IMAGE_TAG)
.\.venv\Scripts\python.exe infra\scripts\seed\register_agents.py --project-endpoint $ep --model $model --gateway-url $mcp   # re-sync agent instructions
```

To set up a GitHub Actions pipeline:

```powershell
azd pipeline config --provider github
```

This creates a federated credential and the repository secrets, and runs `azd provision` + `azd deploy` on every push to `main`. Protect `main` with required reviews, and use a separate `edgeiq-dev` environment for pre-production.

---

## 14. Rollback and teardown

```powershell
# Roll back an app to its previous revision
az containerapp revision list -g $rg -n $api -o table
az containerapp ingress traffic set -g $rg -n $api --revision-weight <previous-revision>=100

# Remove the agents only
.\.venv\Scripts\python.exe infra\scripts\seed\register_agents.py --project-endpoint $ep --delete

# Remove everything (irreversible). --purge also purges soft-deleted Key Vault / Cognitive Services
azd down --purge
```

The following are **not** removed by `azd down`; delete them manually:

- the Fabric workspace items;
- the Entra app registrations (`EdgeIQ-WorkIQ`, `EdgeIQ-Web`);
- the Copilot Studio agent, the custom connector and the Teams app.

---

## 15. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `InsufficientQuota` during `azd up` | Model TPM quota too low in the region | Lower `AZURE_OPENAI_CHAT_CAPACITY`, request more quota, or change `AZURE_LOCATION` |
| `AuthorizationFailed` creating role assignments | Missing Owner / User Access Administrator | Get the role (Step 1.1 #2) and rerun `azd provision` |
| `MissingSubscriptionRegistration` | Provider not registered | Run Step 1.3 |
| MCP gateway shows the placeholder "hello world" page | Image not pushed | Step 5.1 |
| Answers mention "demo mode" | `EDGEIQ_DEMO_MODE` still true, or the orchestrator ID is missing | Step 6.4 |
| Foundry agent answers are generic or wrong | Agents registered without `--gateway-url`, so they have no MCP tools | Rerun Step 6.3 with the public `MCP_GATEWAY_URL` |
| `PermissionDenied` from Foundry at runtime | Managed identity lacks Azure AI Developer | Step 6.5 |
| Fabric layer shows `placeholder` | `FABRIC_WORKSPACE_ID` / `FABRIC_KQL_ENDPOINT` not set, or MI not a workspace member | Steps 8.1 and 8.6 |
| Work IQ `403 Forbidden` | Admin consent not granted | Step 9.1 #5 |
| Copilot Studio connector test returns 401 | Easy Auth enabled but connector not using OAuth | Step 10.1 #5 and 11.1 #4 |
| `git push` looks like it failed in PowerShell | git writes progress to stderr | Check `git status -sb` |
| `ModuleNotFoundError: api` locally | `PYTHONPATH` not set | `$env:PYTHONPATH = "src"` |

---

*Maintainer: Anusha Samineni · anusha.samineni@outlook.com*
