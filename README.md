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
| worker-browser (Camoufox, 12 сессий, GeoIP, kill дерева процессов) | реализован, покрыт тестами; в compose не описан, живой прогон не зафиксирован |
| HTTP → browser escalation (`ERR_NEEDS_BROWSER`, один прыжок) | работает |
| capabilities/heartbeat, `/readyz` с проверкой лейнов | работает (heartbeat не влияет на продажу типов — см. `docs/architecture.md`) |
| canary: фикстуры, type health, webhook | работает, фикстур ещё нет |
| api-key hash at rest (HMAC-SHA256 + pepper), квоты, netguard, redaction | работает |
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
7-дневной canary.

Инвентарь на 2026-11-15: 62 типа в каталоге, 60 записей в `solvers/loader.py`, 58
загружаются без `--extra cv` (60/62 типов; `slider` и `rotate` требуют cv2), 62/62 с экстрой.

## Быстрый старт

Python пинится `.python-version` (3.14) — `uv` поставит его сам. Активировать venv не
нужно: все команды идут через `uv run`.

~~~bash
uv sync --extra dev            # база + тулинг
cp .env.example .env
make dev                       # compose: redis + postgres + minio + migrate + gateway + workers
make seed                      # создать аккаунт и напечатать clientKey
~~~

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
make test       pytest                        655 passed, 3 skipped (тяжёлые требуют --extra cv)
make check      всё вышеперечисленное
~~~

CI: `.github/workflows/ci.yml`, Python 3.14.

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