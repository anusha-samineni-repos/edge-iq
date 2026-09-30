#!/usr/bin/env bash
# =============================================================================
# Run every Edge IQ post-provision seed step in the correct order.
#
# After `azd up`:
#     bash ./infra/scripts/seed/seed-all.sh
#
# Each step degrades gracefully - if a service endpoint is absent the step
# reports what it would have done and exits 0, because Edge IQ runs fully in
# demo mode with no Azure resources. Safe to run on a laptop.
#
# Options:
#     --dry-run         shape and validate, write nothing to Azure
#     --skip-fabric     skip the Lakehouse seed
#     --skip-generate   reuse the existing dataset
#     --days N          days of telemetry history (default 30)
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"

PYTHON="$REPO/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || command -v python)"

DRY=""
SKIP_FABRIC=0
SKIP_GENERATE=0
DAYS=30

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)       DRY="--dry-run" ;;
    --skip-fabric)   SKIP_FABRIC=1 ;;
    --skip-generate) SKIP_GENERATE=1 ;;
    --days)          DAYS="$2"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

step() { printf '\n\033[36m=== %s\033[0m\n' "$1"; }

# --- pull azd environment values if available --------------------------------
if command -v azd >/dev/null 2>&1; then
  step "Reading azd environment"
  if azd env get-values >/tmp/edgeiq.env 2>/dev/null; then
    set -a; . /tmp/edgeiq.env; set +a
    rm -f /tmp/edgeiq.env
    printf '\033[32m  loaded\033[0m\n'
  else
    printf '\033[33m  no azd environment selected - using ambient variables\033[0m\n'
  fi
fi

# --- 1. generate the demo dataset --------------------------------------------
if [ "$SKIP_GENERATE" -eq 0 ]; then
  step "Generating reference data and $DAYS days of telemetry"
  "$PYTHON" "$REPO/data/generators/generate_reference.py"
  "$PYTHON" "$REPO/data/generators/generate_telemetry.py" --days "$DAYS"
fi

# --- 2. Cosmos DB: operational registry --------------------------------------
step "Seeding Cosmos DB (assets, alarms, work orders, compliance)"
"$PYTHON" "$HERE/seed_cosmos.py" $DRY

# --- 3. Azure AI Search: Foundry IQ knowledge index --------------------------
step "Building the Foundry IQ knowledge index"
"$PYTHON" "$HERE/seed_search_index.py" $DRY

# --- 4. Fabric Lakehouse: reference + history tables -------------------------
if [ "$SKIP_FABRIC" -eq 0 ]; then
  step "Staging the Fabric Lakehouse tables"
  MODE="local"
  if [ -n "${FABRIC_WORKSPACE_NAME:-}" ] && [ -z "$DRY" ]; then MODE="onelake"; fi
  "$PYTHON" "$HERE/seed_fabric.py" --mode "$MODE"
fi

# --- 5. Foundry: register the agent topology ---------------------------------
step "Registering the Foundry agents"
"$PYTHON" "$HERE/register_agents.py" $DRY

# --- 6. Copilot Studio connector ---------------------------------------------
step "Generating the Copilot Studio connector"
"$PYTHON" "$HERE/generate_connector.py"

printf '\n\033[32mEdge IQ seeding complete.\033[0m\n\n'
printf '\033[33mNext:\033[0m\n'
echo "  - Run the Fabric notebooks in order: 00_seed -> 01_bronze_to_silver -> 02_silver_to_gold"
echo "  - Import copilot-studio/generated/edgeiq-connector.yaml as a Power Platform custom connector"
echo "  - Upload copilot-studio/declarative-agent.json to Copilot Studio"
echo "  - Verify end to end:  python tests/smoke_api.py"
