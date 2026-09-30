<#
.SYNOPSIS
    Run every Edge IQ post-provision seed step in the correct order.

.DESCRIPTION
    Reads connection details from the azd environment when one is present, so
    after `azd up` you can simply run:

        .\infra\scripts\seed\seed-all.ps1

    Each step is independently runnable and each degrades gracefully: if a
    service endpoint is absent the step reports what it would have done and
    exits 0, because Edge IQ runs fully in demo mode with no Azure resources
    at all. That means this script is safe to run on a laptop.

.PARAMETER DryRun
    Shape and validate everything, write nothing to Azure.

.PARAMETER SkipFabric
    Skip the Lakehouse seed (the slowest step).

.PARAMETER Days
    Days of telemetry history to generate. Default 30.

.EXAMPLE
    .\seed-all.ps1 -DryRun
    .\seed-all.ps1 -Days 90
#>
[CmdletBinding()]
param(
    [switch] $DryRun,
    [switch] $SkipFabric,
    [switch] $SkipGenerate,
    [int]    $Days = 30
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here '..\..\..')

# Prefer the repo venv so the seeders get pandas/pyarrow without polluting the
# machine python.
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

function Write-Step([string] $Text) {
    Write-Host ''
    Write-Host "=== $Text" -ForegroundColor Cyan
}

# --- pull azd environment values if available -------------------------------
if (Get-Command azd -ErrorAction SilentlyContinue) {
    Write-Step 'Reading azd environment'
    try {
        azd env get-values | ForEach-Object {
            if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?(.*?)"?\s*$') {
                [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
            }
        }
        Write-Host '  loaded' -ForegroundColor Green
    } catch {
        Write-Host '  no azd environment selected - using ambient variables' -ForegroundColor Yellow
    }
}

$dry = if ($DryRun) { '--dry-run' } else { $null }

# --- 1. generate the demo dataset -------------------------------------------
if (-not $SkipGenerate) {
    Write-Step "Generating reference data and $Days days of telemetry"
    & $python (Join-Path $repo 'data\generators\generate_reference.py')
    if ($LASTEXITCODE -ne 0) { throw 'Reference generation failed.' }
    & $python (Join-Path $repo 'data\generators\generate_telemetry.py') --days $Days
    if ($LASTEXITCODE -ne 0) { throw 'Telemetry generation failed.' }
}

# --- 2. Cosmos DB: operational registry -------------------------------------
Write-Step 'Seeding Cosmos DB (assets, alarms, work orders, compliance)'
& $python (Join-Path $here 'seed_cosmos.py') $dry
if ($LASTEXITCODE -ne 0) { throw 'Cosmos seed failed.' }

# --- 3. Azure AI Search: Foundry IQ knowledge index -------------------------
Write-Step 'Building the Foundry IQ knowledge index'
& $python (Join-Path $here 'seed_search_index.py') $dry
if ($LASTEXITCODE -ne 0) { throw 'Search index seed failed.' }

# --- 4. Fabric Lakehouse: reference + history tables ------------------------
if (-not $SkipFabric) {
    Write-Step 'Staging the Fabric Lakehouse tables'
    $mode = if ($env:FABRIC_WORKSPACE_NAME -and -not $DryRun) { 'onelake' } else { 'local' }
    & $python (Join-Path $here 'seed_fabric.py') --mode $mode
    if ($LASTEXITCODE -ne 0) { throw 'Fabric seed failed.' }
}

# --- 5. Foundry: register the agent topology --------------------------------
Write-Step 'Registering the Foundry agents'
& $python (Join-Path $here 'register_agents.py') $dry
if ($LASTEXITCODE -ne 0) { throw 'Agent registration failed.' }

# --- 6. Copilot Studio connector --------------------------------------------
Write-Step 'Generating the Copilot Studio connector'
& $python (Join-Path $here 'generate_connector.py')
if ($LASTEXITCODE -ne 0) { throw 'Connector generation failed.' }

Write-Host ''
Write-Host 'Edge IQ seeding complete.' -ForegroundColor Green
Write-Host ''
Write-Host 'Next:' -ForegroundColor Yellow
Write-Host '  - Run the Fabric notebooks in order: 00_seed -> 01_bronze_to_silver -> 02_silver_to_gold'
Write-Host '  - Import copilot-studio/generated/edgeiq-connector.yaml as a Power Platform custom connector'
Write-Host '  - Upload copilot-studio/declarative-agent.json to Copilot Studio'
Write-Host '  - Verify end to end:  python tests/smoke_api.py'
