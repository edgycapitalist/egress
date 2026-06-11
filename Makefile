# Egress developer commands. Run `make` or `make help` for the list.
# Most work happens against the deterministic baseline and cached replay, so it
# costs nothing and needs no cloud credentials.

.DEFAULT_GOAL := help
.PHONY: help init start stop restart test lint fmt build eval deploy check-prereqs

PYTHON ?= python3
COMPOSE ?= docker compose

help: ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

init: ## Install the project (all extras) and create .env from the example
	$(PYTHON) -m pip install -e ".[all,dev]"
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — fill in your values.")

start: ## Start the local data layer (Postgres + Redis) via docker-compose
	$(COMPOSE) up -d

stop: ## Stop the local data layer
	$(COMPOSE) down

restart: stop start ## Restart the local data layer

test: ## Run the offline test suite (no network, no credentials)
	$(PYTHON) -m pytest

lint: ## Lint with ruff
	$(PYTHON) -m ruff check .

fmt: ## Format with ruff
	$(PYTHON) -m ruff format .

build: ## Build all service container images
	docker build --target engine          -t egress-engine .
	docker build --target market_data_mcp -t egress-market-data-mcp .
	docker build --target news_mcp        -t egress-news-mcp .
	docker build --target gateway         -t egress-gateway .

eval: ## Run agent evals / backtest against a real episode (Phase 4)
	@echo "eval target is wired in Phase 4 (see eval/)."

deploy: ## Deploy agents to Agent Engine and services to Cloud Run (Phase 5)
	@echo "deploy target is wired in Phase 5 (see scripts/deploy.sh and infra/)."

check-prereqs: ## Verify required tooling is installed
	@echo "Checking prerequisites..."
	@command -v $(PYTHON) >/dev/null 2>&1 && echo "  ok: $(PYTHON)" || echo "  MISSING: python3"
	@command -v docker >/dev/null 2>&1 && echo "  ok: docker" || echo "  MISSING: docker"
	@docker compose version >/dev/null 2>&1 && echo "  ok: docker compose" || echo "  MISSING: docker compose"
	@command -v gcloud >/dev/null 2>&1 && echo "  ok: gcloud (needed for deploy)" || echo "  note: gcloud not found (needed only for deploy)"
