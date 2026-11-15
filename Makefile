.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
UV ?= uv
COMPOSE ?= docker compose
COMPOSE_FILES ?= -f docker-compose.yml
COMPOSE_FULL ?= -f docker-compose.yml -f docker-compose.dev.yml --profile full

.PHONY: help sync lint fmt fix typecheck test test-cov imports check migrate seed accounts models expected-models publish-model check-model dev full down build logs clean

help:
	@echo "targets:"
	@echo "  sync         install base deps (ARGS='--extra cv --extra dev' for the full set)"
	@echo "  lint         ruff check"
	@echo "  fmt          ruff format"
	@echo "  fix          ruff check --fix"
	@echo "  typecheck    mypy"
	@echo "  test         pytest"
	@echo "  test-cov     pytest with coverage"
	@echo "  imports      import-linter: enforce layer boundaries"
	@echo "  check        lint + imports + test (what CI runs)"
	@echo "  migrate      apply migrations to POSTGRES_URL (local, no docker)"
	@echo "  seed         create a demo account with balance (local, no docker)"
	@echo "  accounts     list accounts (local, no docker)"
	@echo "  models       list models in the bucket"
	@echo "  expected-models  artifact names the solvers look for"
	@echo "  publish-model    NAME=rotate-v1 VERSION=v1 PATH=./rotate.pt"
	@echo "  check-model      NAME=rotate-v1 (download, verify sha256, optional LOAD=1)"
	@echo "  smoke            KEY=<clientKey> TYPE=friendly_captcha — один живой запрос через шлюз"
	@echo "  dev          compose up: redis + postgres + minio + migrate + gateway + workers"
	@echo "  full         dev + js-sidecar + prometheus + grafana"
	@echo "  down         compose down"
	@echo "  build        compose build"
	@echo "  logs         tail the gateway and workers"
	@echo "  clean        drop caches and build artefacts"

sync:
	$(UV) sync $(ARGS)

lint:
	$(UV) run ruff check src tests

fmt:
	$(UV) run ruff format src tests

fix:
	$(UV) run ruff check --fix src tests

typecheck:
	$(UV) run mypy src

test:
	$(UV) run pytest -q

test-cov:
	$(UV) run pytest --cov=captcha --cov-report=term-missing

imports:
	$(UV) run lint-imports

check: lint imports test

migrate:
	$(UV) run python -m captcha.services.cli init-db

seed:
	$(UV) run python -m captcha.services.cli create-account --name demo --balance 10

accounts:
	$(UV) run python -m captcha.services.cli list-accounts

models:
	$(UV) run python -m captcha.services.cli list-models

expected-models:
	$(UV) run python -m captcha.services.cli expected-models

publish-model:
	@test -n "$(NAME)" || (echo "NAME is required, e.g. NAME=rotate-v1"; exit 2)
	@test -n "$(VERSION)" || (echo "VERSION is required, e.g. VERSION=v1"; exit 2)
	@test -n "$(PATH)" || (echo "PATH is required, e.g. PATH=./rotate.pt"; exit 2)
	$(UV) run python -m captcha.services.cli publish-model $(NAME) $(PATH) --version $(VERSION) $(if $(LABELS),--labels $(LABELS),)

check-model:
	@test -n "$(NAME)" || (echo "NAME is required"; exit 2)
	$(UV) run python -m captcha.services.cli check-model $(NAME) $(if $(VERSION),--version $(VERSION),) $(if $(LOAD),--load,)

smoke:
	@test -n "$(KEY)" || (echo "KEY is required, e.g. KEY=<clientKey from make seed>"; exit 2)
	$(UV) run python -m captcha.services.cli smoke --url $(or $(URL),http://localhost:8080) --key $(KEY) $(if $(TYPE),--type $(TYPE),) $(if $(LEGACY),--legacy,)

dev:
	$(COMPOSE) $(COMPOSE_FILES) up -d --build
	@echo "gateway:  http://localhost:8080/docs"
	@echo "minio:    http://localhost:9001"
	@echo "account:  make seed"

full:
	$(COMPOSE) $(COMPOSE_FULL) up -d --build

down:
	$(COMPOSE) $(COMPOSE_FULL) down

build:
	$(COMPOSE) $(COMPOSE_FILES) build

logs:
	$(COMPOSE) $(COMPOSE_FILES) logs -f gateway worker-http worker-cv worker-protocol

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache models-cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
