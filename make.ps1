<#
.SYNOPSIS
    PowerShell equivalent of the project Makefile, for Windows hosts without `make`.

.DESCRIPTION
    Mirrors every target of the Makefile so that the developer experience is identical
    on Windows, Linux and macOS (NFR-ENV-003).

.EXAMPLE
    .\make.ps1 help
    .\make.ps1 validate
    .\make.ps1 up-core
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help',

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# --- Helpers ----------------------------------------------------------------

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "OK  $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "!   $Message" -ForegroundColor Yellow }

function Invoke-Py {
    # Run a command inside the project environment. Tools (ruff, mypy, pytest) and the
    # workspace packages live in the uv-managed venv, not in the system Python, so
    # everything goes through `uv run`. When `uv` is not on PATH it is reachable as a
    # module of the system interpreter (`pip install uv` does not always add it).
    param([Parameter(Mandatory)][string[]]$Arguments)
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run @Arguments
    }
    else {
        & python -m uv run @Arguments
    }
}

function Test-PyModule {
    # Native executables do not raise catchable exceptions, so optional tooling
    # must be probed explicitly rather than wrapped in try/catch.
    # The probe runs in the PROJECT environment: probing the system Python reported
    # ruff, mypy and pytest as missing even after `uv sync`, which made `ci` skip
    # steps silently. $LASTEXITCODE is restored so that a negative probe does not
    # leak into the script's own exit status.
    param([Parameter(Mandatory)][string]$Module)
    $previous = $LASTEXITCODE
    $probe = "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$Module') else 1)"
    Invoke-Py @('python', '-c', $probe) *> $null
    $found = $LASTEXITCODE -eq 0
    $global:LASTEXITCODE = $previous
    return $found
}

function Show-NotYet {
    param([string]$Name, [int]$Phase)
    Write-Host ""
    Write-Warn "'$Name' arrives in Phase $Phase - not implemented yet."
    Write-Host "    Current status: see README.md > Feuille de route"
    Write-Host ""
    exit 1
}

$Targets = [ordered]@{
    'setup'            = 'Install tooling, dependencies and git hooks'
    'env'              = 'Create .env from the template if missing'
    'up-core'          = 'Start core services: Kafka, Schema Registry, MinIO, PostgreSQL, Redis (2.1 GB)'
    'up-lakehouse'     = 'Start core + Iceberg, Spark, Trino (6.2 GB)'
    'up-ml'            = 'Start core + MLflow, model serving (2.7 GB)'
    'up-agents'        = 'Start core + Qdrant, agent service (2.8 GB)'
    'up-app'           = 'Start core + API, frontend (3.2 GB)'
    'up-obs'           = 'Start Prometheus, Grafana, Loki, Tempo, Langfuse (2.5 GB)'
    'down'             = 'Stop every service'
    'ps-mem'           = 'Show memory consumed per running container'
    'clean-data'       = 'Delete local data volumes (irreversible)'
    'demo'             = 'Run the full end-to-end scenario (PRD section 8)'
    'demo-data'        = 'Generate and stream synthetic telecom events'
    'lint'             = 'Run ruff (lint + format check)'
    'format'           = 'Auto-fix formatting and lint issues'
    'typecheck'        = 'Run mypy (strict on libs/)'
    'test'             = 'Run unit tests with coverage'
    'test-integration' = 'Run integration tests (requires running services)'
    'codegen'          = 'Regenerate event models and manifest from the Avro contracts'
    'codegen-check'    = 'Fail if generated contract code is stale'
    'refdata'          = 'Regenerate the synthetic reference data (dbt seeds)'
    'refdata-check'    = 'Fail if the committed reference data is stale'
    'validate'         = 'Validate contracts, generated code freshness and architecture rules'
    'security'         = 'Run security scans (secrets, dependencies)'
    'ci'               = 'Run the full local CI pipeline'
    'adr'              = 'Create a new ADR (usage: .\make.ps1 adr 018 my-decision)'
    'help'             = 'Show this help'
}

# --- Targets ----------------------------------------------------------------

switch ($Target) {

    'help' {
        Write-Host ""
        Write-Host "  TelecomX Platform - available targets" -ForegroundColor White
        Write-Host "  ====================================="
        Write-Host ""
        foreach ($key in $Targets.Keys) {
            Write-Host ("  {0,-20} " -f $key) -ForegroundColor Cyan -NoNewline
            Write-Host $Targets[$key]
        }
        Write-Host ""
    }

    'setup' {
        Write-Step 'Installing uv (Python package manager)'
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            python -m pip install --quiet uv
        }
        Write-Step 'Syncing workspace dependencies'
        try { & uv sync --all-extras --dev } catch { python -m pip install --quiet -e ".[dev]" }
        Write-Step 'Installing pre-commit hooks'
        try { & uv run pre-commit install } catch { Write-Warn 'pre-commit not installed - skipped' }
        Write-Ok 'Setup complete. Copy .env.example to .env and set GROQ_API_KEY.'
    }

    'env' {
        if (-not (Test-Path .env)) {
            Copy-Item .env.example .env
            Write-Ok '.env created - fill in GROQ_API_KEY'
        }
        else { Write-Ok '.env is already present' }
    }

    'up-core'      { Show-NotYet 'up-core' 1 }
    'up-lakehouse' { Show-NotYet 'up-lakehouse' 1 }
    'up-ml'        { Show-NotYet 'up-ml' 3 }
    'up-agents'    { Show-NotYet 'up-agents' 4 }
    'up-app'       { Show-NotYet 'up-app' 5 }
    'up-obs'       { Show-NotYet 'up-obs' 7 }
    'demo'         { Show-NotYet 'demo' 4 }
    'demo-data'    { Show-NotYet 'demo-data' 1 }

    'down' {
        Write-Step 'Stopping all services'
        try { & docker compose --profile "*" down --remove-orphans } catch { Write-Warn 'Nothing running' }
    }

    'ps-mem' {
        try {
            & docker stats --no-stream --format "table {{.Name}}`t{{.MemUsage}}`t{{.MemPerc}}`t{{.CPUPerc}}"
        }
        catch { Write-Warn 'Docker is not running' }
    }

    'clean-data' {
        Write-Warn 'This deletes ALL local lakehouse data.'
        $confirm = Read-Host 'Type "yes" to confirm'
        if ($confirm -ne 'yes') { Write-Host 'Aborted.'; break }
        try { & docker compose --profile "*" down -v --remove-orphans } catch { }
        foreach ($p in '.docker-data', 'data/raw', 'data/bronze', 'data/silver', 'data/gold') {
            if (Test-Path $p) { Remove-Item -Recurse -Force $p }
        }
        Write-Ok 'Local data cleaned'
    }

    'lint' {
        Write-Step 'Running ruff'
        if (-not (Test-PyModule 'ruff')) { Write-Warn 'ruff not installed - run .\make.ps1 setup'; break }
        Invoke-Py @('ruff', 'check', '.')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Invoke-Py @('ruff', 'format', '--check', '.')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Ok 'Lint passed'
    }

    'format' {
        Write-Step 'Formatting'
        if (-not (Test-PyModule 'ruff')) { Write-Warn 'ruff not installed - run .\make.ps1 setup'; break }
        Invoke-Py @('ruff', 'check', '--fix', '.')
        Invoke-Py @('ruff', 'format', '.')
        Write-Ok 'Formatted'
    }

    'typecheck' {
        Write-Step 'Running mypy'
        $paths = @('libs', 'data-platform/generators/src', 'apps', 'agents', 'ml-platform') | Where-Object {
            (Test-Path $_) -and (Get-ChildItem $_ -Recurse -Filter *.py -ErrorAction SilentlyContinue)
        }
        if (-not $paths) { Write-Warn 'No Python packages to check yet (Phase 1)'; break }
        if (-not (Test-PyModule 'mypy')) { Write-Warn 'mypy not installed - run .\make.ps1 setup'; break }
        Invoke-Py (@('mypy') + $paths)
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    'test' {
        Write-Step 'Running unit tests'
        if (-not (Get-ChildItem tests -Recurse -Filter 'test_*.py' -ErrorAction SilentlyContinue)) {
            Write-Warn 'No tests yet (Phase 1)'; break
        }
        if (-not (Test-PyModule 'pytest')) { Write-Warn 'pytest not installed - run .\make.ps1 setup'; break }
        Invoke-Py @('pytest', '-m', 'not integration and not e2e and not llm', '--cov', '--cov-report=term-missing')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    'test-integration' {
        Write-Step 'Running integration tests'
        Invoke-Py @('pytest', '-m', 'integration')
    }

    'codegen' {
        Write-Step 'Generating event models and manifest from the Avro contracts'
        Invoke-Py @('python', 'data-platform/schemas/generate.py')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Ok 'Generated code is up to date - commit it together with the .avsc'
    }

    'codegen-check' {
        Write-Step 'Checking generated contract code is up to date'
        Invoke-Py @('python', 'data-platform/schemas/generate.py', '--check')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    'refdata' {
        Write-Step 'Generating reference data into data-platform/dbt/seeds'
        Invoke-Py @('python', '-m', 'telecomx_generators.reference')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Ok 'Reference data is up to date - commit it with the generator change'
    }

    'refdata-check' {
        Write-Step 'Checking reference data is up to date'
        Invoke-Py @('python', '-m', 'telecomx_generators.reference', '--check')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    'validate' {
        Write-Step 'Validating Avro data contracts'
        & python scripts/validate_contracts.py
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Step 'Checking generated contract code is up to date'
        Invoke-Py @('python', 'data-platform/schemas/generate.py', '--check')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Step 'Checking reference data is up to date'
        Invoke-Py @('python', '-m', 'telecomx_generators.reference', '--check')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Step 'Checking architecture import rules'
        Invoke-Py @('lint-imports')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Ok 'Validation passed'
    }

    'security' {
        Write-Step 'Scanning for secrets'
        if (Get-Command gitleaks -ErrorAction SilentlyContinue) { & gitleaks detect --no-banner --redact }
        else { Write-Warn 'gitleaks not installed - skipped' }
        Write-Step 'Auditing Python dependencies'
        if (Test-PyModule 'pip_audit') { Invoke-Py @('pip_audit') }
        else { Write-Warn 'pip-audit not installed - skipped' }
    }

    'ci' {
        foreach ($t in 'validate', 'lint', 'typecheck', 'test', 'security') {
            & $PSCommandPath $t
            if ($LASTEXITCODE -ne 0) { Write-Host "CI failed at '$t'" -ForegroundColor Red; exit 1 }
        }
        Write-Host ""
        Write-Host "  Local CI passed" -ForegroundColor Green
        Write-Host ""
    }

    'adr' {
        if ($Rest.Count -lt 2) {
            Write-Host 'Usage: .\make.ps1 adr <number> <kebab-case-title>' -ForegroundColor Red
            exit 1
        }
        $n = $Rest[0]; $title = $Rest[1]
        $dest = "docs/adr/ADR-$n-$title.md"
        Copy-Item docs/adr/TEMPLATE.md $dest
        Write-Ok "Created $dest - remember to add it to docs/adr/README.md"
    }

    default {
        Write-Host "Unknown target '$Target'." -ForegroundColor Red
        Write-Host "Run '.\make.ps1 help' to list available targets."
        exit 1
    }
}

# Reaching this point means the target completed without an explicit failure
# exit. Return 0 rather than whatever $LASTEXITCODE a probe left behind.
exit 0
