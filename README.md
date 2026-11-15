# captcha

HTTP-first решалка капч с API, совместимым с 2captcha/capsolver. Одна Python-дистрибуция,
слои с односторонними зависимостями, четыре лейна (http / cv / protocol / browser),
солверы-плагины по семействам.

~~~
POST /createTask     ->  {taskId}
POST /getTaskResult  ->  {status, solution}
POST /getBalance
POST /in.php         ->  OK|<taskId>      (legacy form API)
POST /res.php        ->  OK|<token>       (legacy form API)

GET  /account        ->  кабинет: баланс, леджер, пополнение, статистика
~~~

## Статус

| часть | состояние |
|---|---|
| gateway: createTask / getTaskResult / getBalance, авторизация, биллинг | работает |
| официальные имена типов 2captcha/capsolver, `proxyType/Address/Port/Login/Password` | работает |
| legacy API: `/in.php` + `/res.php`, 15 методов | работает |
| reportCorrect / reportIncorrect, GET-варианты, callbackUrl (pingback) | работает |
| кабинет: баланс, леджер транзакций, пополнение, статистика по типам | работает (провайдер оплаты — заглушка `manual`) |
| очередь (Redis Streams, DLQ, ретраи), хранилище (Postgres + Alembic) | работает |
| worker-http / worker-cv / worker-protocol | работают |
| worker-browser (Camoufox, 12 сессий, GeoIP, kill дерева процессов) | реализован, покрыт тестами; в compose за профилем `browser`, живой прогон — job `browser` в CI |
| TLS-ингресс (Caddy: 80/443, ACME или внутренний CA) | работает: сервис `caddy`, gateway наружу не публикуется |
| HTTP → browser escalation (`ERR_NEEDS_BROWSER`, один прыжок) | работает |
| capabilities/heartbeat, `/readyz` с проверкой лейнов | работает: типы `browser` и browser-gated (`needs_browser`) продаются только при свежем heartbeat браузерного лейна, остальные — из реестра (`docs/architecture.md`) |
| canary: фикстуры, type health, webhook | работает; фикстуры есть на 10 типов из 62 (`tests/canary/fixtures`) |
| `ALLOW_EXPERIMENTAL_TASKS` — гейт experimental-типов (§7) | по умолчанию `0`: experimental-only типы не продаются; `1` — явный beta-оверрайд |
| api-key hash at rest (HMAC-SHA256 + pepper), квоты, redaction | работает |
| netguard (SSRF-проверка клиентских URL) | работает: gateway (`validate_task_urls` до списания), transport (`guard.py`/`redirects.py` на каждом hop) и навигация browser-лейна; тесты — `tests/gateway/test_netguard_urls.py`, `tests/adapters/test_netguard*.py` |
| транспорт curl_cffi, JA3-профили, JS-песочница, пул прокси, реестр весов | работает |
| проекция решения в ключи 2captcha по типу | работает |
| солверы: pow, ocr, audio, slider, rotate, detection, classification, hcaptcha | реализованы, нужны веса для CV |
| солверы: score_based, recaptcha, funcaptcha | частично (`# FILL:` в коде) |
| солверы: antibot (12 движков), interactive (17 вендоров) | протокол написан, wire-константы не сняты (`experimental`), `# FILL:` в коде |
| сборка payload-ов движков (`engines/`) | каркас, `# FILL:` в коде |

Маркеры `# FILL:` — это не забытый код, а места, куда ложится результат реверса:
схема полей, порядок, кодировка. Найти все: `grep -rn "FILL:" src/captcha`.

**62/62 — финальный гейт, не текущее состояние.** Уровни L0–L3, пороги L3 и правило
«продаём только L3» — [docs/plan.md](docs/plan.md) §1–2. Сейчас ни один тип не имеет
7-дневной canary: в репозитории 10 фикстур на 10 типов (`tests/canary/fixtures`).

Инвентарь на 2026-11-15: 63 значения `CaptchaType` (62 вендорских + кастомный
`hopscotch`), 60 записей в `solvers/loader.py`; все 60 импортируются без `--extra cv`,
покрытие типов — 63/63. `slider` решает на numpy-бэкенде, `rotate` (как detection- и
classification-солверы) отвечает на задаче `ERR_CONFIG` без `cv2`/торча/весов.
`experimental=True` у 42 из 60 солверов; экспериментальными (без другого солвера)
остаются 38 из 62 вендорских типов. Лейны: http 25, cv 28 (27 вендорских +
`hopscotch`), protocol 2, browser 8.

## Быстрый старт

Python пинится `.python-version` (3.14) — `uv` поставит его сам. Активировать venv не
нужно: все команды идут через `uv run`.

~~~bash
uv sync --extra dev            # база + тулинг
cp .env.example .env
make dev                       # compose: redis + postgres + minio + migrate + gateway + workers
make seed                      # создать аккаунт и напечатать clientKey
~~~

`make dev` поднимает dev-оверлей: gateway слушает `127.0.0.1:8080`, Caddy выключен
(профиль `edge`). Прод-стек — `docker compose up` без оверлея: наружу публикуется только
`caddy` (80/443, TLS по `CAPTCHA_DOMAIN`), gateway доступен лишь в сети compose.

Первая задача:

~~~bash
curl -s localhost:8080/createTask -H 'content-type: application/json' -d '{
  "clientKey": "<ключ из make seed>",
  "task": {"type": "friendly_captcha", "websiteURL": "https://example.com", "websiteKey": "x"}
}'
# -> {"errorId":0,"taskId":"..."}
~~~

Официальное имя типа тоже принимается:

~~~bash
curl -s localhost:8080/createTask -H 'content-type: application/json' -d '{
  "clientKey": "<ключ>",
  "task": {"type": "HCaptchaTaskProxyless", "websiteURL": "https://authenticate.riotgames.com", "websiteKey": "<sitekey>"}
}'
# -> {"errorId":0,"taskId":"..."}   (лейн browser, до 240 с)
~~~

Без докера, по одному процессу (нужны Redis и Postgres; `S3_*` — только для реестра весов):

~~~bash
make migrate                                   # схема в head
make seed                                      # аккаунт
uv run python -m captcha.services.gateway --port 8080
uv run python -m captcha.services.worker_http --consumers 8 --concurrency 512
uv run python -m captcha.services.worker_cv
uv run python -m captcha.services.worker_protocol
uv run python -m captcha.services.worker_browser --consumers 1   # нужен camoufox
uv run python -m captcha.services.canary --once                  # нужны фикстуры
~~~

Браузерному воркеру нужен `camoufox` (браузер скачивается при первом запуске) и ~700 МБ
RAM на активную сессию: `WORKER_BROWSER_SESSIONS=12` рассчитан на 16 ГБ.

## Структура

~~~
src/captcha/
├── domain/       чистое ядро: типы, состояния, ошибки, цены, форма решения (только stdlib)
├── application/  сценарии поверх портов
├── adapters/     redis, postgres, curl, v8, s3, прокси, профили, браузер, netguard
├── engines/      сборка payload-ов по антибот-движкам (реверс)
├── solvers/      плагины по семействам капч
├── crypto/ behavior/ observability/   листовые утилиты
└── services/     процессы: композиция + транспорт (gateway, 4 воркера, canary, pipeline, cli)
~~~

Правила и полное дерево — [docs/architecture.md](docs/architecture.md).

## Документация

- [AGENTS.md](AGENTS.md) — карта репозитория и правила для ИИ-агентов (и людей)
- [SECURITY.md](SECURITY.md) — модель безопасности: ключи, квоты, SSRF, redaction, TLS
- [docs/architecture.md](docs/architecture.md) — слои, четыре лейна, браузер, capabilities, canary, CV
- [docs/protocol.md](docs/protocol.md) — API, имена типов, прокси, hCaptcha, коды ошибок, форма решения
- [docs/plan.md](docs/plan.md) — гейт 62/62, L0–L3, пороги L3, Riot/Steam/Epic, роли, инфра
- [docs/operations.md](docs/operations.md) — эксплуатация: health, метрики, бэкапы, откат
- [docs/hcaptcha-sites.md](docs/hcaptcha-sites.md) — живая матрица hCaptcha: Steam/Riot/контроль, HTTP pass rate
- [docs/production-contract.md](docs/production-contract.md) — закон для W1 (владелец lead)
- [docs/baseline.md](docs/baseline.md) — снимок состояния на 2026-11-15
- [docs/workflow.md](docs/workflow.md) — git, GitHub, приватный репо, доступ напарнику
- [docs/ml.md](docs/ml.md) — какие веса учить, формат артефактов, как подключить модель
- [docs/ml-tz.md](docs/ml-tz.md) — ТЗ на ML: данные, обучение, метрики приёмки
- [docs/ml-research.md](docs/ml-research.md) — обзор моделей, лицензий, VRAM/latency
- [docs/adr-vision-architecture.md](docs/adr-vision-architecture.md) — ADR-001: visual core + heads
- [docs/implementation-order.md](docs/implementation-order.md) — порядок реализации 62 типов
- [runbooks/README.md](runbooks/README.md) — индекс runbook-ов по 62 типам + шаблон

## Проверки

~~~
make lint       ruff                          clean
make imports    import-linter                 6 contracts kept, 0 broken
make test       pytest                        2448 passed, 12 skipped (10 — integration и Camoufox smoke, 2 — torch/jsdom)
make check      всё вышеперечисленное
~~~

CI: `.github/workflows/ci.yml`, Python 3.14. Четыре job-а: `checks` (ruff,
lint-imports, полный `pytest` без `tests/integration`, scoped mypy, yamllint + `node --check`),
`integration` (Redis + Postgres, 9 тестов), `browser` (Camoufox-образ,
`pytest -m browser tests/integration/browser`), `security` (gitleaks, trivy, pip-audit).

## Команды

~~~bash
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
~~~