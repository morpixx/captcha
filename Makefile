.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
UV ?= uv
COMPOSE ?= docker compose

.PHONY: help sync hooks lint fmt fix typecheck test test-cov imports dev dev-full down build canary migrations clean

help:
	@echo "targets:"
	@echo "  sync         install base deps (add ARGS='--extra cv --extra dev')"
	@echo "  hooks        enable .githooks (blocks direct pushes to main)"
	@echo "  lint         ruff check"
	@echo "  fmt          ruff format"
	@echo "  fix          ruff check --fix"
	@echo "  typecheck    mypy"
	@echo "  test         pytest"
	@echo "  test-cov     pytest with coverage"
	@echo "  imports      import-linter: enforce layer boundaries"
	@echo "  mutations    alembic upgrade head"
	@echo "  dev          docker compose up (gateway + workers + infra)"
	@echo "  dev-full     dev + browser worker + js-sidecar + canary + monitoring"
	@echo "  down         docker compose down"
	@echo "  build        docker compose build"
	@echo "  canary       run the canary suite once against CAPTCHA_GATEWAY_URL"
	@echo "  clean        drop caches and build artefacts"

sync:
	$(UV) sync $(ARGS)

hooks:
	git config core.hooksPath .githooks
	@echo "git hooks enabled: .githooks/pre-push blocks direct pushes to main"

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

mutations:
	$(UV) run alembic -c src/captcha/adapters/storage/alembic.ini upgrade head

dev:
	$(COMPOSE) -f docker-compose.yml up -d --build
	@echo "gateway:  http://localhost:8080"
	@echo "admin:    http://localhost:8081"
	@echo "minio:    http://localhost:9001"

dev-full:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml --profile full up -d --build

down:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml --profile full down

build:
	$(COMPOSE) -f docker-compose.yml build

canary:
	$(UV) run python -m captcha.services.canary --once

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache models-cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
