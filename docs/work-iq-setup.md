# Work IQ setup

Work IQ connects Edge IQ to Microsoft 365, so answers carry organisational
memory alongside telemetry and standards. This is the layer that knows a
technician already flagged the coupling three weeks ago.

Work IQ is optional. Without it Edge IQ runs on Fabric IQ and Foundry IQ and
reports `placeholder` for the Work IQ layer.

---

## What it retrieves

| Source | Content |
|---|---|
| **Teams** | Channel messages, operational chat |
| **SharePoint** | Site documents, procedures, reports |
| **Outlook** | Mail relevant to the asset or site |
| **Planner** | Tasks and maintenance plans |
| **Copilot connectors** | Custom line-of-business content |

---

## Identity model — read this first

Work IQ uses the **On-Behalf-Of (OBO)** flow. The user's token is exchanged for
a Graph token, and content is retrieved *as that user*.

The consequence: **existing Microsoft 365 permissions apply unchanged.** Edge IQ
cannot surface a document the asker could not already open. There is no
re-implementation of M365 authorisation and therefore no bug in that
reimplementation to become a data leak.

The alternative — a service principal with application permissions — would give
Edge IQ tenant-wide read access and make it responsible for filtering. That is a
larger security surface for no functional gain.

---

## 1. Register the application

```bash
az ad app create --display-name "Edge IQ Work IQ" \
  --sign-in-audience AzureADMyOrg
```

Record the **Application (client) ID** and **Directory (tenant) ID**.

---

## 2. Grant Graph permissions

**Delegated** permissions (not application):

| Permission | Purpose |
|---|---|
| `User.Read` | Sign-in |
| `Files.Read.All` | SharePoint and OneDrive documents |
| `Sites.Read.All` | SharePoint sites |
| `Mail.Read` | Outlook |
| `ChannelMessage.Read.All` | Teams channel messages |
| `Tasks.Read` | Planner |
| `ExternalItem.Read.All` | Copilot connector content |

```bash
az ad app permission add --id <APP_ID> \
  --api 00000003-0000-0000-c000-000000000000 \
  --api-permissions \
    e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope \
    df85f4d6-205c-4ac5-a5ea-6bf408dba283=Scope \
    205e70e5-aba6-4c52-a976-6d2d46c48043=Scope \
    570282fd-fa5c-430d-a7fd-fc8dc98a9dca=Scope
```

Grant admin consent:

```bash
az ad app permission admin-consent --id <APP_ID>
```

Delegated permissions define the *ceiling*. The user's own permissions still
apply beneath it.

---

## 3. Configure the API

```bash
azd env set M365_TENANT_ID     <tenant-id>
azd env set M365_CLIENT_ID     <app-id>
azd env set WORK_IQ_ENABLED    true
```

Optional, for a Copilot connector:

```bash
azd env set M365_GRAPH_CONNECTOR_ID <connector-id>
```

---

## 4. Configure OBO token exchange

The API needs a client secret or certificate to perform the exchange. Store it
in Key Vault — never in configuration:

```bash
az keyvault secret set \
  --vault-name <your-keyvault> \
  --name work-iq-client-secret \
  --value <secret>
```

The API resolves it through managed identity at startup. A certificate is
preferable to a secret in production; both are supported by the same flow.

---

## 5. Pass the user token

Work IQ needs a bearer token on the request:

```http
POST /api/chat
Authorization: Bearer <user-access-token>
```

Extracted by `_user_assertion()` in `chat.py`. Without it, Work IQ returns a
placeholder — the request still succeeds on the other two layers.

**From the operator console:** add MSAL and attach the token.
**From Copilot Studio:** the connector passes the user's token automatically
when configured for Entra ID authentication.
**From SCADA or service integrations:** these usually have no user context.
Either omit the token and accept Work IQ degradation, or use a dedicated service
account and accept that the account's permissions define what is visible.

---

## 6. Verify

```bash
curl localhost:8000/api/health
```

Look for:

```json
{ "layerStatus": { "work_iq": "ok" } }
```

Then ask a question with known M365 context and confirm citations reference
Microsoft 365 content.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `work_iq: placeholder` | No tenant configured, or no user token | Set `M365_TENANT_ID` and pass `Authorization` |
| `work_iq: error` | OBO exchange failing | Check the client secret and that admin consent is granted |
| Empty results, status `ok` | Permissions granted but the user has no access | Expected — OBO respects the user's own permissions |
| `AADSTS65001` | Consent missing | Re-run `admin-consent` |
| `AADSTS50013` | Assertion expired | Token lifetime too short; refresh before calling |

---

## Microsoft 365 source placeholders

`copilot-studio/declarative-agent.json` contains `<PLACEHOLDER>` tokens for
tenant-specific M365 sources — SharePoint site IDs, Teams channel IDs, connector
IDs. Populate them from your azd environment:

```bash
python infra/scripts/seed/generate_connector.py
```

The script substitutes known values and **reports any placeholder it could not
resolve** rather than emitting a silently broken manifest. Genuine tenant values
must be filled in manually; the script tells you which ones remain.

---

## Running without Work IQ

Perfectly reasonable, and the recommended first deployment:

```bash
azd env set WORK_IQ_ENABLED false
```

Edge IQ runs on Fabric IQ and Foundry IQ. Operational and knowledge grounding
are unaffected; you lose organisational memory only. Enable Work IQ once the app
registration and consent are in place — layers are independently switchable
precisely so a rollout can be staged.

---

## Related

- [IQ layers](./iq-layers.md)
- [Copilot Studio setup](./copilot-studio-setup.md)
- [Configuration reference](./configuration.md)
