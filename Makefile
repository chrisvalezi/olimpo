.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up up-tools up-obs down logs ps migrate seed test lint fmt shell psql redis reset build restart worker-logs check-env check-docs

help: ## Mostra esta ajuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

check-env: ## Verifica se o .env existe
	@test -f .env || (echo "❌ .env não encontrado. Rode: cp .env.example .env" && exit 1)
	@# O container roda como uid 1000; o diretório montado precisa ser dele,
	@# senão o modo dry-run não consegue gravar o que teria sido publicado.
	@mkdir -p dryrun && chown -R 1000:1000 dryrun 2>/dev/null || true

up: check-env ## Sobe a stack principal
	$(COMPOSE) up -d --build
	@echo "✅ Painel: http://localhost:3000  |  API: http://localhost:8000/docs"

up-tools: check-env ## Sobe também Adminer e Flower
	$(COMPOSE) --profile tools up -d --build

up-obs: check-env ## Sobe também Prometheus e Grafana
	$(COMPOSE) --profile observability up -d --build

down: ## Derruba a stack (mantém os volumes)
	$(COMPOSE) down

restart: ## Reinicia api, worker e beat
	$(COMPOSE) restart api worker beat

build: ## Rebuild das imagens
	$(COMPOSE) build --no-cache

logs: ## Logs de tudo
	$(COMPOSE) logs -f --tail=100

worker-logs: ## Logs só do worker
	$(COMPOSE) logs -f --tail=100 worker

ps: ## Estado dos containers
	$(COMPOSE) ps

migrate: ## Aplica migrações pendentes
	$(COMPOSE) run --rm migrate

seed: ## Popula com dados de exemplo (1 influencer)
	$(COMPOSE) exec api python -m olimpo.db.seed

test: ## Roda os testes
	$(COMPOSE) exec api pytest -q

check-docs: ## Verifica links internos quebrados na documentação
	@python3 scripts/check_docs.py

lint: ## ruff + mypy
	$(COMPOSE) exec api ruff check src/
	$(COMPOSE) exec api mypy src/

fmt: ## Formata o código
	$(COMPOSE) exec api ruff format src/ tests/
	$(COMPOSE) exec api ruff check --fix src/ tests/

shell: ## Shell no container da API
	$(COMPOSE) exec api bash

psql: ## psql no banco
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-olimpo} -d $${POSTGRES_DB:-olimpo}

redis: ## redis-cli
	$(COMPOSE) exec redis redis-cli

reset: ## ⚠️ Apaga TODOS os volumes e recria do zero
	@read -p "Isto apaga o banco inteiro. Confirma? [y/N] " ok && [ "$$ok" = "y" ]
	$(COMPOSE) down -v
	$(COMPOSE) up -d --build
