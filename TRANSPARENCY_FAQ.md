# Transparency FAQ

## What is Edge IQ?

A multi-agent solution accelerator that answers operational questions about a
water utility's IoT Edge estate. It combines telemetry, engineering standards
and organisational context into grounded answers with citations.

It is a reference implementation, not a product.

## What can it do?

Diagnose equipment condition against published standards, evaluate water quality
compliance, analyse district-metered-area flow for leakage, assess pump energy
efficiency, and report on edge device fleet health. It drafts maintenance work
orders for human approval.

## What is it intended for?

Demonstrating and prototyping agentic patterns over industrial data. It is
intended to be adapted — with real data, real validation and appropriate human
oversight — not deployed unchanged into operations.

## What is it *not* intended for?

- **Autonomous control.** It issues no commands to plant or SCADA.
- **Regulatory submission.** Compliance output is advisory. Official reporting
  must come from validated systems.
- **Safety decisions.** It is not a safety-instrumented system and must not be
  placed in a safety function.
- **Unattended action.** Writes are gated on human approval by design.

## How was it evaluated?

Six automated smoke suites cover the IQ layers, MCP servers, API routing,
container layout, console module graph and scenario integrity. All run against
generated data.

The cloud path — Foundry agent runtime, Fabric notebook execution, Copilot
Studio integration — is implemented but requires validation at first deployment.
[next-steps.md](./next-steps.md) lists these explicitly.

## What data does it use?

By default, entirely synthetic data from `data/generators/`. No real utility
data ships with this repository.

When connected to real systems, it reads telemetry, asset records, work orders
and — if Work IQ is enabled — Microsoft 365 content the asking user can already
access.

## How does it handle permissions?

Work IQ uses the On-Behalf-Of flow, so Microsoft 365 content is retrieved as the
asking user. Existing permissions apply unchanged; Edge IQ cannot surface a
document the user could not already open.

Knowledge base content is filtered at query time by classification level, capped
by `EDGEIQ_MAX_CLASSIFICATION`. Above-ceiling content is never retrieved.

## What are the known limitations?

- **Recommendations require verification.** Outputs are grounded and cited, but
  language models can misinterpret context. Citations exist so a human can
  check.
- **Quality depends on grounding.** Wrong rated power in the asset registry
  produces wrong severity classifications. The system is only as good as its
  reference data.
- **Demo data is simplified.** Real estates are messier — more missing data,
  more sensor faults, more ambiguity.
- **Degradation is silent by design.** A missing layer reports `placeholder` and
  the answer proceeds without it. This is visible in the response, but a user
  not looking may not notice the answer is narrower than it could be.
- **Thresholds are jurisdiction-specific.** Regulatory limits default to US-EPA.
  Other jurisdictions need their own values loaded.

## What human oversight is expected?

- All writes require explicit approval
- Recommendations should be reviewed by qualified personnel before acting
- Compliance findings should be confirmed against validated systems
- `agent_traces` in Cosmos DB retains routing, tool calls and citations for
  audit

## What about operational safety?

Edge IQ is read-only with respect to plant. It has no path to control systems
and cannot change a setpoint, start a pump or alter a process. Its only write
capability is creating maintenance records, gated twice.

This is a deliberate boundary. A system that reasons over industrial data should
not also be able to act on it without a human in between.

## Who should use it?

Teams with existing industrial data platforms, Azure familiarity, and the domain
expertise to validate its outputs. It assumes a water utility context but the
patterns generalise to other asset-intensive industries.

## Where do I learn more?

Start with [README.md](./README.md), then
[docs/architecture.md](./docs/architecture.md) and
[docs/iq-layers.md](./docs/iq-layers.md).
