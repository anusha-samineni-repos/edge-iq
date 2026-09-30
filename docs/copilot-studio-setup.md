# Copilot Studio setup

Copilot Studio is the front door. It puts Edge IQ in Teams and Microsoft 365
Copilot, where operators already work, rather than in a separate application
they have to remember exists.

Artifacts: `copilot-studio/`

---

## How it fits

```
Teams / M365 Copilot
        │
   Copilot Studio agent
        │  custom connector (REST)
        ▼
   Solution Orchestrator  →  specialists  →  MCP  →  systems
```

Copilot Studio handles channels, authentication and conversational surface.
**All reasoning stays in the orchestrator.** Routing logic is not duplicated in
topics — a second copy of the routing rules would drift from the first within a
release or two.

---

## 1. Generate the connector package

The manifests ship with `<PLACEHOLDER>` tokens. Populate them from your azd
environment:

```bash
python infra/scripts/seed/generate_connector.py
```

Output lands in `copilot-studio/generated/`:

| File | Purpose |
|---|---|
| `edgeiq-connector.yaml` | OpenAPI definition for the custom connector |
| `apiProperties.json` | Connector metadata and auth configuration |
| `declarative-agent.json` | Agent manifest with M365 sources |

The script **reports any placeholder it could not resolve** rather than emitting
a manifest that imports cleanly and fails at runtime. Tenant-specific values
(SharePoint site IDs, Teams channel IDs) must be filled in by hand; the script
tells you which.

Preview without writing:

```bash
python infra/scripts/seed/generate_connector.py --dry-run
```

---

## 2. Import the custom connector

1. [Power Apps](https://make.powerapps.com) → **Custom connectors** → **New** →
   **Import an OpenAPI file**
2. Upload `edgeiq-connector.yaml`
3. **Security** → **Azure Active Directory**:
   - Client ID: your app registration
   - Resource URL: the API's application ID URI
4. **Create connector**
5. **Test** with a sample request before going further

Entra ID authentication is what makes Work IQ work end to end — the user's token
flows through the connector to the orchestrator, which exchanges it on-behalf-of
for Graph. Choosing API key authentication instead breaks that chain, and Work
IQ silently returns placeholders.

---

## 3. Create the agent

1. [Copilot Studio](https://copilotstudio.microsoft.com) → **Create** → **New agent**
2. Name it (default schema name `edgeiq_water_edge_agent`)
3. **Settings → Generative AI → Generative (orchestration)**

Generative orchestration matters: it lets the agent send the whole question to
the connector rather than pattern-matching it against topic triggers. Classic
orchestration would need a topic per question shape, which no one maintains.

---

## 4. Add the connector as an action

1. **Actions** → **Add an action** → your custom connector
2. Select the `chat` operation
3. Map inputs:

| Input | Source |
|---|---|
| `message` | User's message |
| `conversationId` | Conversation ID |
| `maxClassification` | Static, or from user role |

4. Map the response to the agent's reply, preserving citations

---

## 5. Add Microsoft 365 knowledge sources

**Knowledge** → **Add knowledge**:

- **SharePoint** — O&M document libraries
- **Microsoft Graph connectors** — indexed line-of-business content
- **Dataverse** — if maintenance records live there

These complement Work IQ rather than replacing it. Copilot Studio knowledge is
retrieved by Copilot Studio; Work IQ is retrieved by the orchestrator with the
question's full technical context. The second usually produces better results
for equipment questions — but both respect the user's permissions, so neither
can leak.

---

## 6. Import topics

`copilot-studio/topics/` contains starter topics:

| Topic | Trigger |
|---|---|
| `ask-edge-iq.yaml` | General question → orchestrator |
| `fleet-briefing.yaml` | Estate-wide status summary |
| `work-order-approval.yaml` | Approving a drafted work order |

`ask-edge-iq.yaml` is the important one. It routes open-ended questions to the
orchestrator rather than apologising, which is what makes the agent feel
open-ended instead of scripted.

Import via **Topics** → **Add a topic** → **From file**.

---

## 7. Publish

1. **Publish** in Copilot Studio
2. **Channels** → enable **Microsoft Teams** and **Microsoft 365 Copilot**
3. Submit for admin approval if your tenant requires it

---

## 8. Verify

In Teams, work through:

1. "What can you help me with?" → capability summary
2. "How is pump WTP-01-PUMP-003?" → vibration analysis with citations
3. "Show me chlorine compliance at RES-03" → breach detail
4. Something outside scope → graceful fallback, not a wrong answer

If citations are missing, the response mapping in step 4 dropped them.

---

## Secrets

Two Key Vault secrets, referenced by name only:

| Variable | Default secret name |
|---|---|
| `COPILOT_STUDIO_DIRECTLINE_SECRET_REF` | `copilot-directline-secret` |
| `COPILOT_STUDIO_INBOUND_KEY_REF` | `copilot-inbound-key` |

```bash
az keyvault secret set --vault-name <kv> --name copilot-inbound-key --value <key>
```

The configuration holds the *name*, never the value. The API resolves it through
managed identity at startup, so rotating a secret needs no redeployment.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Connector test 401 | Auth misconfigured | Check client ID and resource URL |
| Agent replies generically | Action not wired | Confirm the action is added and mapped |
| No citations | Response mapping | Map the citations field explicitly |
| Work IQ placeholder | Not using Entra auth | Switch the connector to Azure AD |
| `<PLACEHOLDER>` at runtime | Value never resolved | Re-run `generate_connector.py`, fill reported gaps |

---

## Related

- [Work IQ setup](./work-iq-setup.md)
- [Architecture](./architecture.md)
- [Deployment guide](./deployment.md)
