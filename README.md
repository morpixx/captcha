# captcha

HTTP-first решалка капч с API, совместимым с 2captcha/capsolver. Одна Python-дистрибуция,
слои с односторонними зависимостями, солверы-плагины по семействам.

```
POST /createTask     ->  {taskId}
POST /getTaskResult  ->  {status, solution}
POST /getBalance
POST /in.php         ->  OK|<taskId>      (legacy form API)
POST /res.php        ->  OK|<token>       (legacy form API)

GET  /account        ->  кабинет: баланс, леджер, пополнение, статистика
```

## Статус

| часть | состояние |
|---|---|
| gateway: createTask / getTaskResult / getBalance, авторизация, биллинг | работает |
| legacy API: `/in.php` + `/res.php`, 15 методов | работает |
| reportCorrect / reportIncorrect, GET-варианты, callbackUrl (pingback) | работает |
| кабинет: баланс, леджер транзакций, пополнение, статистика по типам | работает (провайдер оплаты — заглушка `manual`) |
| очередь (Redis Streams, DLQ, ретраи), хранилище (Postgres + Alembic) | работает |
| worker-http / worker-cv / worker-protocol | работают |
| транспорт curl_cffi, JA3-профили, JS-песочница, пул прокси, реестр весов | работает |
| проекция решения в ключи 2captcha по типу | работает |
| солверы: pow, ocr, audio, slider, rotate, detection, classification | реализованы, нужны веса для CV |
| солверы: score_based, recaptcha, funcaptcha | частично (`# FILL:` в коде) |
| солверы: antibot (12 движков), interactive (17 вендоров) | протокол написан, wire-константы не сняты (`experimental`), `# FILL:` в коде |
| сборка payload-ов движков (`engines/`) | каркас, 53 маркера `# FILL:` |

Маркеры `# FILL:` — это не забытый код, а места, куда ложится результат реверса:
схема полей, порядок, кодировка. Найти все: `grep -rn "FILL:" src/captcha`.

Браузерного лейна нет: всё решается по HTTP, JS исполняется в песочнице
(`adapters/jssandbox`). Подробнее — [docs/architecture.md](docs/architecture.md).

## Быстрый старт

Python пинится `.python-version` (3.14) — `uv` поставит его сам. Активировать venv не
нужно: все команды идут через `uv run`.

```bash
uv sync --extra dev            # база + тулинг
cp .env.example .env
make dev                       # compose: redis + postgres + minio + migrate + gateway + workers
make seed                      # создать аккаунт и напечатать clientKey
```

Первая задача:

```bash
curl -s localhost:8080/createTask -H 'content-type: application/json' -d '{
  "clientKey": "<ключ из make seed>",
  "task": {"type": "friendly_captcha", "websiteURL": "https://example.com", "websiteKey": "x"}
}'
# -> {"errorId":0,"taskId":"..."}
```

Без докера, по одному процессу (нужны Redis и Postgres; `S3_*` — только для реестра весов):

```bash
make migrate                                   # схема в head
make seed                                      # аккаунт
uv run python -m captcha.services.gateway --port 8080
uv run python -m captcha.services.worker_http --consumers 8 --concurrency 512
```

## Структура

```
src/captcha/
├── domain/       чистое ядро: типы, состояния, ошибки, цены, форма решения (только stdlib)
├── application/  сценарии поверх портов
├── adapters/     redis, postgres, curl, v8, s3, прокси, профили, pingback
├── engines/      сборка payload-ов по антибот-движкам (реверс)
├── solvers/      плагины по семействам капч
├── crypto/ behavior/ observability/   листовые утилиты
└── services/     процессы: композиция зависимостей + транспорт
```

Правила и полное дерево — [docs/architecture.md](docs/architecture.md).

## Документация

- [AGENTS.md](AGENTS.md) — карта репозитория и правила для ИИ-агентов (и людей)
- [docs/architecture.md](docs/architecture.md) — слои, дерево, правила, что дальше
- [docs/protocol.md](docs/protocol.md) — API, коды ошибок, форма решения
- [docs/workflow.md](docs/workflow.md) — git, GitHub, приватный репо, доступ напарнику
- [docs/ml.md](docs/ml.md) — какие веса учить, формат артефактов, как подключить модель
- [docs/ml-tz.md](docs/ml-tz.md) — ТЗ на ML: данные, обучение, метрики приёмки
- [docs/implementation-order.md](docs/implementation-order.md) — порядок реализации 62 типов
- [docs/plan.md](docs/plan.md) — план: роли, сроки, инфра, бюджет
- [runbooks/](runbooks/) — по файлу на тип/движок

## Проверки

```
make lint       ruff                          clean
make imports    import-linter                 6 contracts kept, 0 broken
make test       pytest                        262 passed, 3 skipped (тяжёлые требуют --extra cv)
make check      всё вышеперечисленное
```

CI: `.github/workflows/ci.yml`, Python 3.14.

## Команды

```bash
make sync        # установка (ARGS='--extra cv --extra dev' для полного набора)
make lint fmt    # ruff
make typecheck   # mypy
make test        # pytest
make imports     # архитектурные контракты (import-linter)
make dev         # поднять стенд
make migrate     # alembic upgrade head (локально)
make seed        # создать аккаунт
make models      # что лежит в бакете моделей
make publish-model NAME=rotate-v1 VERSION=v1 PATH=./rotate.pt
make smoke KEY=<clientKey> TYPE=friendly_captcha   # один живой запрос через шлюз
```
