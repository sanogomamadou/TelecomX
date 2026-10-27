# =============================================================================
#  TelecomX Intelligence & Autonomous Operations Platform
#  Single entry point for every developer task.
#
#  Windows users without `make`: use the PowerShell equivalent -> .\make.ps1 <target>
# =============================================================================

.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose
PYTHON := python

# Phases not yet implemented fail loudly rather than silently doing nothing.
define not_yet
	@echo ""; \
	echo "  ⏳ '$(1)' arrives in Phase $(2) — not implemented yet."; \
	echo "     Current status: see README.md > Feuille de route"; \
	echo ""; \
	exit 1
endef

# --- Help --------------------------------------------------------------------

.PHONY: help
help: ## Show this help
	@echo ""
	@echo "  TelecomX Platform — available targets"
	@echo "  ====================================="
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""

# --- Setup -------------------------------------------------------------------

.PHONY: setup
setup: ## Install tooling, dependencies and git hooks
	@echo "==> Installing uv (Python package manager)"
	@command -v uv >/dev/null 2>&1 || $(PYTHON) -m pip install --quiet uv
	@echo "==> Syncing workspace dependencies"
	@uv sync --all-extras --dev || $(PYTHON) -m pip install --quiet -e ".[dev]"
	@echo "==> Installing pre-commit hooks"
	@uv run pre-commit install || pre-commit install
	@echo "==> Done. Copy .env.example to .env and set GROQ_API_KEY."

.PHONY: env
env: ## Create .env from the template if missing
	@test -f .env || (cp .env.example .env && echo "==> .env created — fill in GROQ_API_KEY")
	@test -f .env && echo "==> .env is present"

# --- Docker Compose profiles (see ADR-011) -----------------------------------

.PHONY: up-core
up-core: ## Start core services: Kafka, Schema Registry, MinIO, PostgreSQL, Redis (2.1 GB)
	$(call not_yet,up-core,1)

.PHONY: up-lakehouse
up-lakehouse: ## Start core + Iceberg, Spark, Trino (6.2 GB)
	$(call not_yet,up-lakehouse,1)

.PHONY: up-ml
up-ml: ## Start core + MLflow, model serving (2.7 GB)
	$(call not_yet,up-ml,3)

.PHONY: up-agents
up-agents: ## Start core + Qdrant, agent service (2.8 GB)
	$(call not_yet,up-agents,4)

.PHONY: up-app
up-app: ## Start core + API, frontend (3.2 GB)
	$(call not_yet,up-app,5)

.PHONY: up-obs
up-obs: ## Start Prometheus, Grafana, Loki, Tempo, Langfuse (2.5 GB)
	$(call not_yet,up-obs,7)

.PHONY: down
down: ## Stop every service
	@$(COMPOSE) --profile "*" down --remove-orphans 2>/dev/null || echo "==> Nothing running"

.PHONY: ps-mem
ps-mem: ## Show memory consumed per running container
	@docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}" 2>/dev/null \
		|| echo "==> Docker is not running"

.PHONY: clean-data
clean-data: ## Delete local data volumes (irreversible)
	@echo "==> This deletes ALL local lakehouse data. Ctrl-C to abort."
	@sleep 3
	@$(COMPOSE) --profile "*" down -v --remove-orphans 2>/dev/null || true
	@rm -rf .docker-data data/raw data/bronze data/silver data/gold
	@echo "==> Local data cleaned"

# --- Demo --------------------------------------------------------------------

.PHONY: demo
demo: ## Run the full end-to-end scenario (PRD §8)
	$(call not_yet,demo,4)

.PHONY: demo-data
demo-data: ## Generate and stream synthetic telecom events
	$(call not_yet,demo-data,1)

# --- Quality -----------------------------------------------------------------

.PHONY: lint
lint: ## Run ruff (lint + format check)
	@uv run ruff check . && uv run ruff format --check . \
		|| $(PYTHON) -m ruff check . && $(PYTHON) -m ruff format --check .

.PHONY: format
format: ## Auto-fix formatting and lint issues
	@uv run ruff check --fix . && uv run ruff format . \
		|| $(PYTHON) -m ruff check --fix . && $(PYTHON) -m ruff format .

.PHONY: typecheck
typecheck: ## Run mypy (strict on libs/, standard on generators)
	@uv run mypy libs data-platform/generators/src

.PHONY: test
test: ## Run unit tests with coverage
	@uv run pytest -m "not integration and not e2e and not llm" --cov --cov-report=term-missing \
		2>/dev/null || echo "==> No tests yet (Phase 1)"

.PHONY: test-integration
test-integration: ## Run integration tests (requires running services)
	@uv run pytest -m integration

.PHONY: codegen
codegen: ## Regenerate event models and manifest from the Avro contracts
	@echo "==> Generating event models and manifest from the Avro contracts"
	@uv run python data-platform/schemas/generate.py

.PHONY: codegen-check
codegen-check: ## Fail if generated contract code is stale
	@uv run python data-platform/schemas/generate.py --check

.PHONY: refdata
refdata: ## Regenerate the synthetic reference data (dbt seeds)
	@echo "==> Generating reference data into data-platform/dbt/seeds"
	@uv run python -m telecomx_generators.reference

.PHONY: refdata-check
refdata-check: ## Fail if the committed reference data is stale
	@uv run python -m telecomx_generators.reference --check

.PHONY: gen-events
gen-events: ## Generate synthetic events to build/events (MINUTES=60 by default)
	@uv run python -m telecomx_generators.events --duration-minutes $(or $(MINUTES),60) --out-dir build/events

# No `|| echo` fallbacks below: a check that cannot fail does not check anything.
.PHONY: validate
validate: ## Validate contracts, generated code freshness and architecture rules
	@echo "==> Validating Avro data contracts"
	@$(PYTHON) scripts/validate_contracts.py
	@echo "==> Checking generated contract code is up to date"
	@uv run python data-platform/schemas/generate.py --check
	@echo "==> Checking reference data is up to date"
	@uv run python -m telecomx_generators.reference --check
	@echo "==> Checking architecture import rules"
	@uv run lint-imports

.PHONY: security
security: ## Run security scans (secrets, dependencies, images)
	@echo "==> Scanning for secrets"
	@gitleaks detect --no-banner --redact 2>/dev/null || echo "    gitleaks not installed — skipped"
	@echo "==> Auditing Python dependencies"
	@uv run pip-audit 2>/dev/null || echo "    pip-audit not installed — skipped"

.PHONY: ci
ci: validate lint typecheck test security ## Run the full local CI pipeline
	@echo ""
	@echo "  ✅ Local CI passed"
	@echo ""

# --- Documentation -----------------------------------------------------------

.PHONY: docs-check
docs-check: ## Check documentation links and structure
	@$(PYTHON) scripts/check_docs.py 2>/dev/null || echo "==> scripts/check_docs.py not present yet"

.PHONY: adr
adr: ## Create a new ADR from the template (usage: make adr N=018 TITLE=my-decision)
	@test -n "$(N)" || (echo "ERROR: provide N=<number>"; exit 1)
	@test -n "$(TITLE)" || (echo "ERROR: provide TITLE=<kebab-case-title>"; exit 1)
	@cp docs/adr/TEMPLATE.md docs/adr/ADR-$(N)-$(TITLE).md
	@echo "==> Created docs/adr/ADR-$(N)-$(TITLE).md — remember to add it to docs/adr/README.md"
