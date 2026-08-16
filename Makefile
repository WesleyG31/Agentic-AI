# Kompass — one-command developer interface.
# On Windows without `make`, run the underlying `python -m ...` commands shown
# in each target (or use `scripts/*.py` directly). See README "Quickstart".

PY ?= python

.DEFAULT_GOAL := help
.PHONY: help install seed demo evals evals-smoke evals-live-baseline release test test-security test-integration load-test reconcile lint fmt ui api local-up local-down clean observability-init observability-up observability-down observability-logs prompts-sync dataset-sync

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime + dev/eval dependencies
	$(PY) -m pip install -r requirements-dev.txt

seed: ## Build the ACME SQLite DB and vector index from corpus/
	$(PY) -m kompass.scripts.seed

demo: ## Run the canonical end-to-end HITL demo (recorrido B)
	$(PY) -m kompass.scripts.demo

evals: ## Run the eval suite and regenerate the README metrics table
	$(PY) -m evals.run

evals-smoke: ## Run deterministic offline evaluation gates (no LLM/network)
	$(PY) -m evals.offline --ci

evals-live-baseline: ## Run the five-category reviewed local Ollama baseline
	$(PY) -m evals.run --case-id rag-01 --case-id sql-01 --case-id multi-01 --case-id action-01 --case-id abstain-01 --record-live-baseline

release: ## Verify and print the reproducible agent release identity
	$(PY) -m kompass.release

test: ## Run the test suite
	$(PY) -m pytest

test-security: ## Run deterministic auth, OIDC, trust, isolation, input, and action tests
	$(PY) -m pytest tests/test_authorization.py tests/test_ingress_security.py tests/test_oidc.py tests/test_security_adversarial.py tests/test_safety.py tests/test_sandbox.py tests/test_trust_boundary.py tests/test_memory.py tests/test_lessons.py tests/test_action_executor.py tests/test_reconciliation.py tests/test_a2a.py tests/test_mcp_v2.py

test-integration: ## Run real local PostgreSQL and Keycloak tests (services must be up)
	KOMPASS_TEST_POSTGRES_ADMIN_DSN='postgresql://postgres:kompass-local-admin-only@127.0.0.1:55432/kompass?sslmode=disable' KOMPASS_TEST_POSTGRES_DSN='postgresql://kompass_app:kompass-local-app-only@127.0.0.1:55432/kompass?sslmode=disable' KOMPASS_TEST_OIDC_ISSUER='http://localhost:8081/realms/kompass' KOMPASS_TEST_OIDC_CONNECT_BASE='http://127.0.0.1:8081' $(PY) -m pytest tests/integration

load-test: ## Run the bounded local OIDC/PostgreSQL load scenario
	$(PY) -m kompass.scripts.load_test --output reports/load-baseline.json

reconcile: ## Reconcile stale local action receipts for TENANT
	$(PY) -m kompass.scripts.reconcile --tenant $(TENANT)

lint: ## Lint with ruff
	$(PY) -m ruff check .

fmt: ## Auto-format with ruff
	$(PY) -m ruff format . && $(PY) -m ruff check --fix .

api: ## Serve the FastAPI app
	$(PY) -m uvicorn kompass.api.app:app --reload --port 8000

local-up: ## Build and start app + PostgreSQL + Keycloak locally
	docker compose up -d --build

local-down: ## Stop the free local stack without deleting data
	docker compose down

ui: ## Launch the Streamlit chat UI
	$(PY) -m streamlit run ui/app.py

observability-init: ## Generate local Langfuse secrets and configure the SDK
	$(PY) -m kompass.scripts.observability_env

observability-up: ## Start Langfuse OSS v4 at http://localhost:3000
	docker compose --env-file .env.observability -f docker-compose.observability.yml up -d

observability-down: ## Stop Langfuse without deleting its persistent data
	docker compose --env-file .env.observability -f docker-compose.observability.yml down

observability-logs: ## Follow Langfuse web and worker logs
	docker compose --env-file .env.observability -f docker-compose.observability.yml logs -f langfuse-web langfuse-worker

prompts-sync: ## Publish code-versioned prompts to Langfuse Prompt Management
	$(PY) -m kompass.scripts.sync_prompts

dataset-sync: ## Publish the 60-case golden dataset to Langfuse
	$(PY) -m evals.sync_dataset

clean: ## Remove local data artifacts and caches
	rm -rf .chroma corpus/acme.db kompass_checkpoints.db .pytest_cache .ruff_cache
