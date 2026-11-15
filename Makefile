.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
UV ?= uv
UVX ?= uvx
COMPOSE ?= docker compose
COMPOSE_FILES ?= -f docker-compose.yml -f docker-compose.dev.yml
COMPOSE_FULL ?= -f docker-compose.yml -f docker-compose.dev.yml --profile full
COMPOSE_INTEGRATION ?= -f tests/integration/docker-compose.yml
K6 ?= k6
NODE ?= node

# Mypy scope that is green today. The rest of the tree is tracked in
# docs/baseline.md; widening this list is the way to grow coverage.
MYPY_SCOPE ?= src/captcha/domain src/captcha/application src/captcha/engines src/captcha/adapters/storage src/captcha/adapters/netguard.py src/captcha/observability/metrics.py

.PHONY: help sync lint fmt fix typecheck typecheck-core test test-cov imports check check-full \
	lint-yaml lint-js test-integration test-integration-up test-integration-down load load-locust \
	migrate seed accounts models expected-models publish-model check-model dev full down build logs clean

help:
	@echo "targets:"
	@echo "  sync             install base deps (ARGS='--extra cv --extra dev' for the full set)"
	@echo "  lint             ruff check"
	@echo "  fmt              ruff format"
	@echo "  fix              ruff check --fix"
	@echo "  typecheck        mypy src (known red, see docs/baseline.md)"
	@echo "  typecheck-core   mypy over the modules that are clean today"
	@echo "  test             pytest"
	@echo "  test-cov         pytest with coverage"
	@echo "  imports          import-linter: enforce layer boundaries"
	@echo "  lint-yaml        yamllint + actionlint over workflows and CI compose"
	@echo "  lint-js          node --check over the JS sidecars"
	@echo "  check            lint + imports + test (what CI runs)"
	@echo "  check-full       check + typecheck-core + yaml/js lint"
	@echo "  test-integration-up    start ephemeral redis + postgres for tests/integration"
	@echo "  test-integration       pytest -m integration (needs the services above; CI service containers work too)"
	@echo "  test-integration-down  remove the integration containers and volumes"
	@echo "  load             k6 createTask scenario, KEY=<clientKey> RATE=1000 DURATION=60s URL=..."
	@echo "  load-locust      locust variant of the same scenario"
	@echo "  migrate          apply migrations to POSTGRES_URL (local, no docker)"
	@echo "  seed             create a demo account with balance (local, no docker)"
	@echo "  accounts         list accounts (local, no docker)"
	@echo "  models           list models in the bucket"
	@echo "  expected-models  artifact names the solvers look for"
	@echo "  publish-model    NAME=rotate-v1 VERSION=v1 PATH=./rotate.pt"
	@echo "  check-model      NAME=rotate-v1 (download, verify sha256, optional LOAD=1)"
	@echo "  smoke            KEY=<clientKey> TYPE=friendly_captcha — one live request through the gateway"
	@echo "  dev              compose up (base + dev overlay): redis + postgres + minio + migrate + gateway + workers"
	@echo "  full             dev + js-sidecar + prometheus + grafana"
	@echo "  down             compose down (base + dev overlay)"
	@echo "  build            compose build"
	@echo "  logs             tail the gateway and workers"
	@echo "  clean            drop caches and build artefacts"

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

typecheck-core:
	$(UV) run mypy $(MYPY_SCOPE)

test:
	$(UV) run pytest -q

test-cov:
	$(UV) run pytest --cov=captcha --cov-report=term-missing

imports:
	$(UV) run lint-imports

lint-yaml:
	$(UVX) yamllint@1.35.1 -c .github/yamllint.yml .github/workflows tests/integration/docker-compose.yml
	$(UVX) --from actionlint-py actionlint

lint-js:
	$(NODE) --check src/captcha/adapters/jssandbox/sidecar_node/server.js
	$(NODE) --check src/captcha/adapters/hcaptcha_js/bundle.js
	$(NODE) --check src/captcha/adapters/hcaptcha_js/realm.js
	$(NODE) --check src/captcha/adapters/hcaptcha_js/server.js

check: lint imports test

check-full: lint imports typecheck-core test lint-yaml lint-js

test-integration-up:
	$(COMPOSE) $(COMPOSE_INTEGRATION) up -d --wait

test-integration:
	INTEGRATION_REQUIRED=1 $(UV) run pytest -q -m integration tests/integration

test-integration-down:
	$(COMPOSE) $(COMPOSE_INTEGRATION) down -v

load:
	@test -n "$(KEY)" || (echo "KEY is required, e.g. make load KEY=<clientKey>"; exit 2)
	$(K6) run \
		--env BASE_URL=$(or $(URL),http://127.0.0.1:8080) \
		--env CLIENT_KEY=$(KEY) \
		--env RATE=$(or $(RATE),1000) \
		--env DURATION=$(or $(DURATION),60s) \
		$(if $(TYPE),--env TYPE=$(TYPE),) \
		tools/load/create_task.js

load-locust:
	@test -n "$(KEY)" || (echo "KEY is required, e.g. make load-locust KEY=<clientKey>"; exit 2)
	CLIENT_KEY=$(KEY) $(UVX) locust -f tools/load/locustfile.py --headless \
		-u $(or $(USERS),1000) -r $(or $(SPAWN),200) \
		--run-time $(or $(DURATION),60s) \
		--host $(or $(URL),http://127.0.0.1:8080)

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
	rm -rf .venv .venv-* .pytest_cache .mypy_cache .ruff_cache models-cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
