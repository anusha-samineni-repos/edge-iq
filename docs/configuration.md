# Configuration reference

Every setting is an environment variable. Nothing is hard-coded, so the same
image runs in demo, dev and production with only configuration changing.

Source of truth: `src/api/python/config.py`

---

## Orchestrator

| Variable | Default | Purpose |
|---|---|---|
| `EDGEIQ_ORCHESTRATOR_NAME` | `Edge IQ Solution Orchestrator` | Display name |
| `EDGEIQ_ORCHESTRATION_MODE` | `magentic` | `magentic` \| `handoff` \| `single` |
| `EDGEIQ_MAX_ROUNDS` | `12` | Agent turn ceiling |
| `EDGEIQ_MAX_STALL_ROUNDS` | `3` | Abort after N rounds with no progress |
| `EDGEIQ_REQUEST_TIMEOUT` | `180` | Seconds per request |
| `EDGEIQ_MCP_GATEWAY_URL` | `http://localhost:8080` | MCP gateway |
| `EDGEIQ_ENABLE_TRACING` | `true` | OpenTelemetry traces |
| `EDGEIQ_MAX_CLASSIFICATION` | `internal` | Governance ceiling |
| `EDGEIQ_DEMO_MODE` | `false` | Answer from bundled data, never call Azure |
| `EDGEIQ_DEMO_DATA_PATH` | `data/customdata` | Demo dataset location |
| `EDGEIQ_ALLOW_WRITES` | `false` | Second key on all write tools |

**`EDGEIQ_ORCHESTRATION_MODE`** — `magentic` plans and delegates across several
specialists; `handoff` routes to exactly one; `single` attaches every tool to one
agent. Latency falls and control falls together, in that order.

**`EDGEIQ_MAX_CLASSIFICATION`** — `public` < `internal` < `confidential` <
`restricted`. Applied as a **query-time filter** in AI Search, so above-ceiling
content is never retrieved rather than retrieved and then scrubbed.

**`EDGEIQ_DEMO_MODE`** — the whole solution runs with no Azure resources. This is
the path the test suite covers.

---

## Foundry IQ — knowledge

| Variable | Default | Purpose |
|---|---|---|
| `AZURE_AI_AGENT_ENDPOINT` | — | Foundry project endpoint |
| `AZURE_AI_FOUNDRY_ACCOUNT` | — | Account name |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `gpt-4o` | Chat model |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-large` | Embeddings |
| `AZURE_SEARCH_ENDPOINT` | — | AI Search endpoint |
| `AZURE_SEARCH_INDEX` | `edgeiq-knowledge` | Index name |
| `AZURE_SEARCH_SEMANTIC_CONFIG` | `edgeiq-semantic` | Semantic config |
| `FOUNDRY_IQ_TOP_K` | `8` | Passages retrieved |
| `FOUNDRY_IQ_RERANKER_THRESHOLD` | `1.8` | Minimum reranker score |
| `FOUNDRY_IQ_ENABLED` | `true` | Layer switch |

`FOUNDRY_IQ_RERANKER_THRESHOLD` is the citation-quality dial. Raise it if
answers cite loosely relevant passages; lower it if grounded questions come back
unanswered. `1.8` on Azure's 0–4 scale is deliberately strict — no citation is
better than a weak one when the answer may trigger a site visit.

Considered configured when `AZURE_AI_AGENT_ENDPOINT` is set.

---

## Fabric IQ — operational data

| Variable | Default | Purpose |
|---|---|---|
| `FABRIC_WORKSPACE_NAME` | `EdgeIQ-WaterUtility` | Workspace |
| `FABRIC_WORKSPACE_ID` | — | Workspace GUID |
| `FABRIC_LAKEHOUSE_NAME` | `EdgeIQ_Lakehouse` | Lakehouse |
| `FABRIC_LAKEHOUSE_ID` | — | Lakehouse GUID |
| `FABRIC_EVENTHOUSE_NAME` | `EdgeIQ_Telemetry` | Eventhouse |
| `FABRIC_KQL_ENDPOINT` | — | KQL query endpoint |
| `FABRIC_KQL_DATABASE` | `EdgeIQ_Telemetry` | KQL database |
| `FABRIC_ONELAKE_ENDPOINT` | `https://onelake.dfs.fabric.microsoft.com` | OneLake |
| `FABRIC_SQL_ENDPOINT` | — | Lakehouse SQL endpoint |
| `FABRIC_DATA_AGENT_URL` | — | Fabric data agent |
| `FABRIC_ONTOLOGY_PATH` | `fabric/ontology/water_utility_ontology.yaml` | Ontology |
| `FABRIC_IQ_ENABLED` | `true` | Layer switch |

Considered configured when **any** of `FABRIC_KQL_ENDPOINT`,
`FABRIC_SQL_ENDPOINT` or `FABRIC_DATA_AGENT_URL` is set — the layer works with a
partial Fabric footprint.

The OneLake `abfss://` URI is derived, not configured:

```
abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{lakehouse}.Lakehouse/Tables
```

---

## Work IQ — Microsoft 365

| Variable | Default | Purpose |
|---|---|---|
| `M365_TENANT_ID` | — | Entra tenant |
| `M365_CLIENT_ID` | — | App registration |
| `M365_GRAPH_ENDPOINT` | `https://graph.microsoft.com/v1.0` | Graph |
| `M365_SHAREPOINT_SITE_URLS` | — | CSV of site URLs |
| `M365_TEAMS_CHANNEL_IDS` | — | CSV of channel IDs |
| `M365_OUTLOOK_SHARED_MAILBOXES` | — | CSV of mailboxes |
| `M365_GRAPH_CONNECTOR_ID` | — | Copilot connector |
| `WORK_IQ_ENABLED` | `true` | Layer switch |

Considered configured when both `M365_CLIENT_ID` and `M365_TENANT_ID` are set.
Full setup: [work-iq-setup.md](./work-iq-setup.md).

---

## Cosmos DB

| Variable | Default |
|---|---|
| `AZURE_COSMOS_ENDPOINT` | — |
| `AZURE_COSMOS_DATABASE` | `edgeiq` |
| `COSMOS_ASSETS_CONTAINER` | `assets` |
| `COSMOS_TELEMETRY_CONTAINER` | `telemetry_summary` |
| `COSMOS_WORKORDERS_CONTAINER` | `workorders` |
| `COSMOS_ALARMS_CONTAINER` | `alarms` |
| `COSMOS_COMPLIANCE_CONTAINER` | `compliance` |
| `COSMOS_MEMORY_CONTAINER` | `agent_memory` |
| `COSMOS_TRACES_CONTAINER` | `agent_traces` |

No key variable — access is via managed identity and RBAC.

---

## Copilot Studio

| Variable | Default |
|---|---|
| `COPILOT_STUDIO_ENVIRONMENT_ID` | — |
| `COPILOT_STUDIO_AGENT_SCHEMA_NAME` | `edgeiq_water_edge_agent` |
| `COPILOT_STUDIO_DIRECTLINE_SECRET_REF` | `copilot-directline-secret` |
| `COPILOT_STUDIO_INBOUND_KEY_REF` | `copilot-inbound-key` |

The `*_REF` variables are **Key Vault secret names, not secrets.** Resolved at
runtime through managed identity.

---

## Edge / IoT

| Variable | Default |
|---|---|
| `AZURE_IOTHUB_NAME` | — |
| `AZURE_IOTHUB_HOSTNAME` | — |
| `AZURE_EVENTHUB_NAMESPACE` | — |
| `AZURE_EVENTHUB_TELEMETRY` | `edge-telemetry` |
| `AZURE_EVENTHUB_CONSUMER_GROUP` | `edgeiq-agents` |

A dedicated consumer group keeps agent reads from competing with the Fabric
eventstream for offsets.

---

## Platform

| Variable | Purpose |
|---|---|
| `AZURE_APPCONFIG_ENDPOINT` | App Configuration |
| `AZURE_KEY_VAULT_ENDPOINT` | Key Vault |
| `AZURE_CLIENT_ID` | Managed identity client ID |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry |
| `ALLOWED_ORIGINS` | CORS origins |

---

## Setting variables

**azd (recommended):**

```bash
azd env set EDGEIQ_ORCHESTRATION_MODE handoff
azd env set FOUNDRY_IQ_TOP_K 12
azd env refresh        # re-read what provisioning wrote back
```

**Local:** a `.env` in the repo root, or export directly.

**Container Apps:** environment variables on the container; secrets as Key Vault
references.

---

## Minimum viable configurations

**Demo — nothing else required:**

```bash
EDGEIQ_DEMO_MODE=true
```

**Foundry IQ only:**

```bash
AZURE_AI_AGENT_ENDPOINT=https://<project>.services.ai.azure.com/api/projects/<p>
AZURE_SEARCH_ENDPOINT=https://<search>.search.windows.net
FABRIC_IQ_ENABLED=false
WORK_IQ_ENABLED=false
```

**Full three-layer:** all of Foundry IQ, plus one Fabric endpoint, plus both
M365 identifiers.

---

## Reading current state

```bash
curl localhost:8000/api/health
```

Returns `describe()` — every layer's `enabled` and `configured` state without
exposing values. `enabled: true, configured: false` means the switch is on but
the endpoints are missing; that layer will degrade to a placeholder.

---

## Related

- [Deployment guide](./deployment.md)
- [IQ layers](./iq-layers.md)
- [Work IQ setup](./work-iq-setup.md)
