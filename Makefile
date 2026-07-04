# Kompass — one-command developer interface.
# On Windows without `make`, run the underlying `python -m ...` commands shown
# in each target (or use `scripts/*.py` directly). See README "Quickstart".

PY ?= python

.DEFAULT_GOAL := help
.PHONY: help install seed demo evals test lint fmt up down ui api clean

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

test: ## Run the test suite
	$(PY) -m pytest

lint: ## Lint with ruff
	$(PY) -m ruff check .

fmt: ## Auto-format with ruff
	$(PY) -m ruff format . && $(PY) -m ruff check --fix .

api: ## Serve the FastAPI app
	$(PY) -m uvicorn kompass.api.app:app --reload --port 8000

ui: ## Launch the Streamlit chat UI
	$(PY) -m streamlit run ui/app.py

up: ## Start optional infra (Qdrant, Postgres, Langfuse)
	docker compose up -d

down: ## Stop infra
	docker compose down

clean: ## Remove local data artifacts and caches
	rm -rf .chroma corpus/acme.db kompass_checkpoints.db .pytest_cache .ruff_cache
