# Deployment guide

Three paths, in order of commitment: run it locally with no Azure at all, deploy
to a dev environment, or deploy to production.

---

## Path 1 — Local, no Azure

Everything runs against generated data. This is also the path covered by the
test suite, so it is the one known to work.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r src/api/python/requirements.txt
pip install -r src/mcp_servers/requirements.txt

python data/generators/generate_reference.py
python data/generators/generate_telemetry.py --days 30 --seed 7
```

Start the MCP gateway and the API in separate terminals:

```bash
# terminal 1
set PYTHONPATH=src
python -m uvicorn mcp_servers.gateway:app --port 8080

# terminal 2
set EDGEIQ_DEMO_MODE=true
set PYTHONPATH=src/api/python
python -m uvicorn app:app --port 8000 --reload
```

Optional console (needs Node 20+):

```bash
cd src/App
npm install
npm run dev          # http://localhost:3000
```

Verify:

```bash
curl localhost:8080/health
curl localhost:8000/api/health
pytest
```

---

## Path 2 — Dev environment on Azure

```bash
azd auth login
azd env new edgeiq-dev
azd up
```

`azd up` provisions everything in `infra/`, builds the three containers and
deploys them. Expect 15–25 minutes, most of it Fabric capacity and Cosmos.

### Post-provision

```powershell
.\infra\scripts\seed\seed-all.ps1
```

```bash
./infra/scripts/seed/seed-all.sh
```

This runs, in order:

1. `generate_reference.py` — sites, assets, devices, tags
2. `generate_telemetry.py` — seeded telemetry history
3. `seed_cosmos.py` — asset registry, alarms, work orders, compliance
4. `seed_search_index.py` — build and populate the Foundry IQ index
5. `seed_fabric.py` — load lakehouse tables
6. `register_agents.py` — register agents with Foundry
7. `generate_connector.py` — produce the Copilot Studio package

`seed_cosmos.py`, `seed_search_index.py` and `register_agents.py` support
`--dry-run`; passing `-DryRun` to the wrapper forwards it to all three. Use it
first if you want to see what will be written.

---

## Path 3 — Production

Everything from path 2, plus the following.

### Changing parameters

All infrastructure parameters live in `infra/main.parameters.json`. Common ones:

| Parameter | Purpose | Default |
|---|---|---|
| `solutionName` | Prefix for all resource names | `edgeiq` |
| `environmentName` | Drives SKU sizing defaults | `dev` |
| `location` | Azure region | resource group location |
| `cosmosServerless` | Serverless vs provisioned | `true` |
| `cosmosThroughput` | RU/s per container when provisioned | `1000` |
| `fabricCapacitySku` | Fabric SKU | `F2` |
| `deployFabricCapacity` | Create capacity, or reuse existing | `true` |
| `searchSku` | AI Search tier | `basic` |
| `chatModelCapacity` | Chat model TPM (thousands) | `50` |
| `iotHubSku` | IoT Hub tier | `S1` |
| `minReplicas` / `maxReplicas` | Container scale bounds | `0` / `3` |
| `imageTag` | Container image tag | `latest` |

Override without editing the file:

```bash
azd env set AZURE_LOCATION eastus2
azd env set COSMOS_SERVERLESS false
azd env set COSMOS_THROUGHPUT 10000
```

### Production checklist

- [ ] `cosmosServerless: false` with sized `cosmosThroughput` — serverless has a
      per-container ceiling that real telemetry will reach
- [ ] `minReplicas ≥ 2` — `0` means cold starts on an operational tool
- [ ] `searchSku: standard` — `basic` caps index size and replica count
- [ ] `EDGEIQ_ALLOW_WRITES` — decide deliberately; leave `false` until approval
      workflows are proven
- [ ] `EDGEIQ_MAX_CLASSIFICATION` — set the governance ceiling for your tenant
- [ ] Work IQ configured — see [work-iq-setup.md](./work-iq-setup.md)
- [ ] `fabricAdminMembers` populated — otherwise only the deployer can administer
      the capacity
- [ ] Alerts on Application Insights
- [ ] Fabric capacity sized for real telemetry volume, not demo volume
- [ ] Cosmos backup policy reviewed
- [ ] `ALLOWED_ORIGINS` set to your actual origins, not `*`

### Sizing Fabric

`F2` is a demo capacity. For production, size against telemetry rate:

| Devices | Sample rate | Suggested |
|---|---|---|
| < 500 | 1/min | F4 |
| 500–5,000 | 1/min | F8 |
| 5,000–20,000 | 1/min | F16 |
| > 20,000 | 1/min | F32+ |

Eventhouse ingestion is usually the constraint, not query load.

---

## Containers

Three images, built by `azd up` or directly:

| Image | Dockerfile | Port |
|---|---|---|
| API | `ApiApp.Dockerfile` | 8000 |
| Console | `WebApp.Dockerfile` | 3000 |
| MCP gateway | `McpApp.Dockerfile` | 8080 |

Build and push manually:

```powershell
.\infra\scripts\build\build-and-push-acr.ps1 -Registry <acr-name> -Tag v1.0.0
```

```bash
./infra/scripts/build/build-and-push-acr.sh --registry <acr-name> --tag v1.0.0
```

Uses ACR build tasks when available, falling back to local Docker.

> **Directory layout is load-bearing.** The API and MCP modules resolve the repo
> root by walking up from their own file, so the images preserve the `src/`
> depth rather than flattening it. Flattening breaks knowledge and ontology
> loading silently — the app starts, it just answers worse.
> `tests/smoke_containers.py` guards this.

---

## Deploying a single service

```bash
azd deploy api
azd deploy web
```

`azure.yaml` defines two deployable services. The MCP gateway is built from
`McpApp.Dockerfile` and deployed as part of the API service's container
environment.

---

## Verifying a deployment

```bash
curl https://<api-fqdn>/api/health
```

```json
{
  "status": "ready",
  "mode": "magentic",
  "layerStatus": {
    "work_iq": "ok",
    "fabric_iq": "ok",
    "foundry_iq": "ok"
  }
}
```

A layer reporting `demo` or `placeholder` in production means its configuration
did not land. Check the corresponding `*_ENABLED` flag and endpoint variables.

Then run a demo scenario from [demo-guide.md](./demo-guide.md) end to end — a
green health check only proves the services started.

---

## Teardown

```bash
azd down --purge
```

`--purge` matters: without it, Key Vault and Foundry accounts go into soft-delete
and block redeployment with the same name.

---

## Related

- [Configuration reference](./configuration.md)
- [Architecture](./architecture.md)
- [Copilot Studio setup](./copilot-studio-setup.md)
- [Demo guide](./demo-guide.md)
