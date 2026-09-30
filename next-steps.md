# Next steps

Edge IQ ships with a generated water utility estate. Adapting it to a real one
is mostly configuration and data mapping rather than code.

---

## 1. Connect real telemetry

Replace the generated data with your own.

1. Point the eventstream at your IoT Hub
   (`fabric/eventstream/edgeiq-telemetry-eventstream.json`)
2. Adjust the cleaning rules in `fabric/notebooks/01_bronze_to_silver.py` for
   your tag naming and units
3. Update gold aggregates in `02_silver_to_gold.py` if your analytical questions
   differ
4. Unset `EDGEIQ_DEMO_MODE`

The MCP tool contracts do not change, so agents need no modification.

---

## 2. Load your asset registry

Cosmos DB `assets` is the source for criticality, rated power and failure modes.
Rated power in particular drives ISO 10816-3 severity — an incorrect value
produces confidently wrong zone classifications.

Adapt `infra/scripts/seed/seed_cosmos.py` to read from your EAM or CMMS export.

---

## 3. Extend the ontology

`fabric/ontology/water_utility_ontology.yaml` is the highest-leverage file in the
repository. Adding a synonym improves every future question using that term,
with no code change and no redeployment.

Start with the vocabulary your operators actually use — local tag nicknames,
site abbreviations, the informal name for a piece of equipment. Data that isn't
in the ontology is queryable and effectively invisible to natural language.

---

## 4. Replace the knowledge base

`documents/knowledge-base/` contains 25 representative documents. Substitute
your own SOPs, standards and regulatory limits, then re-run:

```bash
python infra/scripts/seed/seed_search_index.py
```

Keep the classification field accurate — it is what the governance filter acts
on.

---

## 5. Connect Microsoft 365

Work IQ is the layer most often deferred and most often missed afterwards. It is
what turns *"vibration is high"* into *"vibration is high, and Dave flagged the
coupling three weeks ago."*

[Work IQ setup](./docs/work-iq-setup.md)

---

## 6. Enable writes deliberately

`EDGEIQ_ALLOW_WRITES=false` is the shipped default. Before changing it:

- Confirm the approval workflow matches your change control process
- Decide who may approve, and enforce it at the identity layer
- Verify `agent_traces` is retained long enough for your audit requirements

---

## 7. Add a use case

The pattern, in order:

1. Add tools to the relevant MCP server
2. Add or extend an agent specification in `registry.py`, granting only the
   tools it needs
3. Add routing keywords
4. Extend the ontology with the new vocabulary
5. Add a knowledge document if the reasoning depends on a standard
6. Add a scenario JSON so the capability is demonstrable and tested
7. Run `pytest`

Step 6 is easy to skip and worth keeping. `smoke_scenarios.py` ties scenarios
back to the generator, the registry, the MCP modules and the CSVs — it is what
catches drift between a capability and its data.

---

## Before production

Items requiring validation at first cloud deploy, since the tested path is demo
mode:

- **`agent-framework` call signatures** in `agents/factory.py` — the Magentic and
  handoff builder calls are written from documentation and unverified against an
  installed package
- **Foundry SDK calls** in `infra/scripts/seed/register_agents.py`
- **Fabric notebook execution** — never run against Spark
- **Eventstream import** — never imported into a live workspace
- **Console bundle** — static checks only, never compiled

Each has a `--dry-run` or equivalent where practical. The production checklist in
[deployment.md](./docs/deployment.md) covers the rest.

---

## Reusing the components

The MCP servers have no Edge IQ dependencies and can be lifted wholesale into
another solution — replace `datasource.py`, keep the tool contracts, and
consuming agents need no changes.

The same applies to the ontology pattern, the governance filter approach and the
two-key write gate. They are not water-specific.
