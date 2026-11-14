.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
UV ?= uv
COMPOSE ?= docker compose

.PHONY: help sync lint fmt typecheck test test-unit test-integration dev dev-full down clean build canary release

help:
	@echo "targets:"
	@echo "  sync              install deps via uv workspace"
	@echo "  lint              ruff check"
	@echo "  fmt               ruff format"
	@echo "  typecheck         mypy"
	@echo "  test              all tests"
	@echo "  test-unit         unit only"
	@echo "  test-integration  integration (needs compose stack)"
	@echo "  dev               docker-compose up: gateway + redis + postgres + minio + worker-http + worker-cv + admin"
	@echo "  dev-full          dev + browser-fallback + js-sidecar"
	@echo "  down              docker-compose down"
	@echo "  build             build all docker images"
	@echo "  canary            run canary suite once"
	@echo "  clean             remove caches and venvs"

sync:
	$(UV) sync --all-packages

lint:
	$(UV) run ruff check .

fmt:
	$(UV) run ruff format .

typecheck:
	$(UV) run mypy packages solvers services

test: test-unit

test-unit:
	$(UV) run pytest tests/unit packages solvers -x -q

test-integration:
	$(UV) run pytest tests/integration -x -q

dev:
	$(COMPOSE) -f docker-compose.yml up -d
	@echo "gateway:  http://localhost:8080"
	@echo "admin:    http://localhost:8081"
	@echo "minio:    http://localhost:9001"
	@echo "postgres: localhost:5432"
	@echo "redis:    localhost:6379"

dev-full:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml --profile full up -d

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build

canary:
	$(UV) run python -m captcha_canary.main --once

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
