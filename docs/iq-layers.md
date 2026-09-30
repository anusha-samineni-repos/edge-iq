# The three IQ layers

Edge IQ's answers come from a unified context layer assembled from three
independent services. This document explains what each one contributes, how to
configure it, how it behaves when unavailable, and how to extend it.

All three are queried concurrently before routing, in
`src/api/python/iq/context.py`.

---

## Why three layers

A single question about a piece of equipment has three different kinds of
answer, and they live in three different places.

> *"Vibration on WTP-01-PUMP-003 is 7.9 mm/s. Is that a problem?"*

- **Fabric IQ** knows the reading, the 12-day trend, and that the pump is rated
  at 75 kW.
- **Foundry IQ** knows that ISO 10816-3 puts 7.9 mm/s in **zone D** for a machine
  of that size — unacceptable, damage is occurring. For a 15–75 kW machine the
  same number is zone C.
- **Work IQ** knows a technician raised a coupling alignment concern in Teams
  three weeks ago.

Any one alone gives a partial answer. The first gives a number without meaning.
The second gives meaning without a subject. The third gives history without
measurement. Together they give a diagnosis.

---

## Work IQ

**What it contributes** — organisational memory. What people have said, decided,
scheduled and documented about the thing being asked about.

**Source** — Microsoft Graph: Teams messages, SharePoint documents, Outlook mail,
Planner tasks, Microsoft 365 Copilot connectors.

**Implementation** — `src/api/python/iq/work_iq.py`

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WORK_IQ_ENABLED` | `true` | Master switch |
| `M365_TENANT_ID` | — | Entra tenant |
| `M365_CLIENT_ID` | — | App registration with Graph permissions |
| `M365_GRAPH_ENDPOINT` | `https://graph.microsoft.com/v1.0` | Graph base URL |
| `M365_GRAPH_CONNECTOR_ID` | — | Optional Copilot connector for custom content |

### Identity model

Work IQ uses the **On-Behalf-Of** flow. The user's bearer token is exchanged for
a Graph token, so content is retrieved *as that user*. Existing Microsoft 365
permissions apply unchanged — Edge IQ cannot surface a document the asker could
not already open.

This is a deliberate choice over a service principal with application
permissions. An app-permission design would need Edge IQ to re-implement M365
authorisation, and any bug in that reimplementation becomes a data leak.

### Degradation

Without `M365_TENANT_ID` or a user token, Work IQ returns a placeholder note and
reports `placeholder` in `layer_status`. The turn proceeds on the other two
layers. The UI shows the layer as unavailable rather than pretending it was
consulted.

**Setup instructions:** [work-iq-setup.md](./work-iq-setup.md)

### Extending

`WorkIQ.search()` returns a `WorkIQResult`. To add a source, add a fetch method
and merge its results — the return shape is what the context block consumes, so
new sources need no orchestrator change.

---

## Fabric IQ

**What it contributes** — the operational truth. Telemetry, trends, asset
records and estate-wide comparison, addressed in business vocabulary rather than
table names.

**Source** — Fabric Lakehouse over OneLake, with an Eventhouse/KQL database for
high-rate telemetry.

**Implementation** — `src/api/python/iq/fabric_iq.py`

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FABRIC_IQ_ENABLED` | `true` | Master switch |
| `FABRIC_WORKSPACE_NAME` | `EdgeIQ-WaterUtility` | Fabric workspace |
| `FABRIC_WORKSPACE_ID` | — | Resolved at provisioning |
| `FABRIC_LAKEHOUSE_NAME` | `EdgeIQ_Lakehouse` | Lakehouse name |
| `FABRIC_LAKEHOUSE_ID` | — | Resolved at provisioning |
| `FABRIC_EVENTHOUSE_NAME` | `EdgeIQ_Telemetry` | Eventhouse for streaming telemetry |
| `FABRIC_KQL_ENDPOINT` | — | KQL query endpoint |
| `FABRIC_KQL_DATABASE` | `EdgeIQ_Telemetry` | KQL database |
| `FABRIC_SQL_ENDPOINT` | — | Lakehouse SQL analytics endpoint |
| `FABRIC_ONELAKE_ENDPOINT` | `https://onelake.dfs.fabric.microsoft.com` | OneLake DFS endpoint |
| `FABRIC_DATA_AGENT_URL` | — | Optional Fabric data agent |
| `FABRIC_ONTOLOGY_PATH` | `fabric/ontology/water_utility_ontology.yaml` | Semantic model |

### The ontology is the interesting part

Fabric IQ does not hand raw table names to a model and hope. It resolves
business vocabulary through a semantic model first.

`resolve_entity()` matches in four tiers, widest to narrowest, first match wins:

1. **Entity name** — `"asset"` → `Asset`
2. **Entity synonym** — `"equipment"` → `Asset`
3. **Attribute allowed values** — `"pump"` → `Asset` (because `pump` is a value
   of `assetType`)
4. **Attribute name or synonym** — `"chlorine"` → `WaterQuality`

The result carries `matchedVia: "<tier>:<detail>"`, so a resolution can always
be explained.

**When a term is not understood, extend the ontology before touching code.**
Most vocabulary gaps are ontology gaps. Adding `"free chlorine"` as a synonym is
a one-line change; adding a code path for it is a maintenance burden.

### Degradation

Without Fabric configuration, Fabric IQ reads the generated CSVs in
`data/customdata` and reports `demo` in `layer_status`. Query semantics are the
same; the data is synthetic.

### Extending

Add tables to the lakehouse, then describe them in the ontology with
`synonyms` and `allowedValues`. The ontology is the contract — anything in it is
addressable in natural language without code changes.

---

## Foundry IQ

**What it contributes** — governed knowledge. Standards, SOPs, regulatory
guidance and runbooks, with citations and access control.

**Source** — Azure AI Search index over `documents/knowledge-base/`.

**Implementation** — `src/api/python/iq/foundry_iq.py`

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FOUNDRY_IQ_ENABLED` | `true` | Master switch |
| `AZURE_SEARCH_ENDPOINT` | — | Search service |
| `AZURE_SEARCH_INDEX` | `edgeiq-knowledge` | Index name |
| `AZURE_SEARCH_SEMANTIC_CONFIG` | `edgeiq-semantic` | Semantic ranker profile |
| `FOUNDRY_IQ_TOP_K` | `8` | Results retrieved |
| `FOUNDRY_IQ_RERANKER_THRESHOLD` | `1.8` | Minimum reranker score |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-large` | Vector embeddings |

### Governance is a filter, not a scrub

Every document carries a classification: `public`, `internal`, `confidential` or
`restricted`. Each request carries a ceiling (`EDGEIQ_MAX_CLASSIFICATION`,
default `internal`, overridable per request).

The ceiling is applied **as a query-time filter predicate in AI Search**, not as
a post-retrieval filter. Content above the ceiling is never retrieved, never
enters the prompt, and never reaches the model. A post-retrieval scrub would
mean restricted text had already been materialised into a context window — one
prompt-injection bug away from disclosure.

Verified in both directions by `tests/smoke_iq.py`.

### Chunking

The knowledge base uses ALLCAPS section labels rather than markdown headings
(`"STANDARD. ISO 10816-3 classifies..."`). The chunker tries markdown headings
first, falls back to a caps-label regex, then coalesces adjacent sections toward
`TARGET_CHARS = 900` with a `MAX_CHARS = 2400` ceiling.

The coalescing step matters. Splitting on every label gave 129 chunks averaging
309 characters — fragments too small to carry context, so retrieval returned
sentences without their conditions. Coalescing produced 48 chunks averaging 899
characters, each self-contained.

Chunking logic: `infra/scripts/seed/seed_search_index.py`.

### Citations

Foundry returns markers in the form `【3:0†source】`. The orchestrator rewrites
these to `[3]` via `_MARKER_RE` and aligns them with the citation list the UI
renders.

### Degradation

Without a Search endpoint, Foundry IQ reads `documents/knowledge-base/` directly
and applies the same classification filter locally. Retrieval is keyword-based
rather than semantic, but governance behaviour is identical — the security
property does not depend on the cloud path.

### Extending

Add a JSON document to `documents/knowledge-base/` (a top-level array of
document objects, each with `classification`), then re-run:

```bash
python infra/scripts/seed/seed_search_index.py
```

---

## Unified context assembly

```python
context = await build_unified_context(
    question,
    foundry_iq=..., fabric_iq=..., work_iq=...,
    user_assertion=...,          # bearer token for Work IQ OBO
    max_classification=...,      # governance ceiling
)
```

All three layers run concurrently under
`asyncio.gather(..., return_exceptions=True)`. Each records its own status. The
result renders to a single prompt block (~10 KB) via `to_prompt_block()`, and
carries the extracted entities the router uses.

### Layer status values

| Value | Meaning |
|---|---|
| `ok` / `ready` | Live, cloud-backed |
| `demo` / `local` | Local fallback in use |
| `placeholder` | Not configured, returning a stub |
| `error` | Attempted and failed — detail in the trace |

Status is surfaced in the UI. Users should know what an answer was built from.

---

## Turning layers off

Each layer has an independent switch:

```bash
WORK_IQ_ENABLED=false      # no M365 tenant yet
FABRIC_IQ_ENABLED=false    # Fabric not provisioned
FOUNDRY_IQ_ENABLED=false   # no Search service
```

A disabled layer reports `disabled` and contributes nothing. This is the
recommended way to stage a rollout — bring layers online as their dependencies
land, rather than waiting for all three.

---

## Related

- [Architecture](./architecture.md)
- [Work IQ setup](./work-iq-setup.md)
- [Data platform](./data-platform.md)
- [Configuration reference](./configuration.md)
