# captcha

HTTP-first решалка капч с API, совместимым с 2captcha/capsolver. Одна Python-дистрибуция,
слои с односторонними зависимостями, солверы-плагины по семействам.

```
POST /createTask  ->  {taskId}
POST /getTaskResult  ->  {status, solution}
POST /getBalance
```

## Статус

| часть | состояние |
|---|---|
| gateway (createTask / getTaskResult / getBalance, авторизация, биллинг) | работает |
| очередь (Redis Streams, DLQ, ретраи), хранилище (Postgres + Alembic) | работает |
| worker-http / worker-cv / worker-protocol / worker-browser | работают |
| транспорт curl_cffi, JA3-профили, JS-песочница, пул прокси, реестр весов | работает |
| проекция решения в ключи 2captcha по типу | работает |
| солверы: pow, ocr, audio, slider, rotate, detection, classification | реализованы |
| солверы: score_based, recaptcha, funcaptcha | частично (`# FILL:` в коде) |
| солверы: antibot (12 движков), interactive (14 вендоров) | каркас, 78 маркеров `# FILL:` |
| сборка payload-ов движков (`engines/`) | каркас, 53 маркера `# FILL:` |

Маркеры `# FILL:` — это не забытый код, а места, куда ложится результат реверса:
схема полей, порядок, кодировка. Найти все: `grep -rn "FILL:" src/captcha`.

## Быстрый старт

```bash
uv sync --extra dev            # база + тулинг
cp .env.example .env           # затем руками создать аккаунт в БД (см. docs/workflow.md)
make dev                       # docker compose: gateway + воркеры + redis + postgres + minio
```

Без докера, по одному процессу из venv:

```bash
uv run python -m captcha.services.gateway --port 8080
uv run python -m captcha.services.worker_http --consumers 8 --concurrency 512
uv run python -m captcha.services.canary --once
```

Нужны Redis (`REDIS_URL`) и Postgres (`POSTGRES_URL`); `S3_*` — только для реестра весов
и worker-cv.

## Структура

```
src/captcha/
├── domain/       чистое ядро: типы, состояния, ошибки, цены, форма решения (только stdlib)
├── application/  сценарии поверх портов
├── adapters/     redis, postgres, curl, v8, s3, прокси, профили, браузер
├── engines/      сборка payload-ов по антибот-движкам (реверс)
├── solvers/      плагины по семействам капч
├── crypto/ behavior/ observability/   листовые утилиты
└── services/     процессы: композиция зависимостей + транспорт
```

Правила и полное дерево — [docs/architecture.md](docs/architecture.md).

## Документация

- [docs/architecture.md](docs/architecture.md) — слои, дерево, правила, что дальше
- [docs/protocol.md](docs/protocol.md) — API, коды ошибок, форма решения
- [docs/workflow.md](docs/workflow.md) — git, GitHub, приватный репо, доступ напарнику
- [docs/implementation-order.md](docs/implementation-order.md) — порядок реализации 62 типов
- [docs/plan.md](docs/plan.md) — план: роли, сроки, инфра, бюджет
- [runbooks/](runbooks/) — по файлу на тип/движок

## Команды

```bash
make sync        # установка (ARGS='--extra cv --extra dev' для полного набора)
make lint fmt    # ruff
make typecheck   # mypy
make test        # pytest
make imports     # архитектурные контракты (import-linter)
make dev         # поднять стенд
make canary      # прогнать регресс один раз
```
