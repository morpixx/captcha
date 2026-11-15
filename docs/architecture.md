# Архитектура

Одна дистрибуция `captcha`: слои с односторонними зависимостями, плагины солверов по
семействам, реверс движков отдельно от солверов.

## Принцип

Ядро — то, что решаем мы и что не может сломать чужой релиз: типы капч, состояния задачи,
цены, коды ошибок, форма решения. Адаптация — то, чья правильность задана чужой спекой:
62 типа, 12 антибот-движков, их payload-схемы. Ядро должно быть маленьким и закрытым,
адаптация — локальной: правка в одном движке не должна тянуть за собой остальные.

**Всё решается по HTTP.** Клиент присылает собранный payload, мы отдаём ответ. Браузерного
лейна нет: JS исполняется в песочнице (`adapters/jssandbox`), TLS-отпечаток даёт curl_cffi.
Если для типа этого не хватает — это баг реверса, а не повод тащить playwright.

## Слои

| слой | что внутри | можно импортировать | нельзя |
|---|---|---|---|
| `domain/` | типы, состояния, ошибки, цены, форма решения, профиль | stdlib | всё остальное |
| `application/` | сценарии: маршрутизация задачи | `domain` | `adapters`, `services` |
| `adapters/` | redis, postgres, curl, v8, s3, прокси, профили | `domain` | `application`, `solvers` |
| `engines/` | сборка payload-ов по движкам | `domain`, `crypto`, `behavior` | `adapters` |
| `solvers/` | плагины по семействам | `domain`, `crypto`, `behavior`, `engines` | `adapters`, `services` |
| `services/` | процессы: композиция + транспорт | всё | — |

Направление ровно такое: `services → application → domain`, а адаптеры реализуют порты
домена. Проверяется `import-linter`: шесть контрактов в `pyproject.toml`.

## Дерево

```
src/captcha/
├── config.py                  Settings (pydantic-settings), читается только в services
├── domain/                    ЧИСТОЕ ЯДРО, без I/O
│   ├── enums.py               CaptchaType (62), AntibotEngine (12), SolveMode, TaskStatus, Priority
│   ├── catalog.py             TypeSpec (lane, price, solution_kind) — одна строка на тип
│   ├── task.py                Task, TaskEnvelope, SolverResult, new_task_id
│   ├── solution.py            проекция SolverResult → ключи 2captcha (dispatch по solution_kind)
│   ├── errors.py              CaptchaError и подтипы, коды ERR_*
│   ├── pricing.py             цены по типам (тонкий фасад над catalog)
│   ├── lanes.py               Lane и pick_lane (какой воркер берёт задачу)
│   └── profile.py             Profile — структура фингерпринта
├── application/
│   └── route_task.py          выбор лейна на входе в очередь
├── adapters/
│   ├── transport/             curl.py, ja_profiles.py, retry.py, response.py
│   ├── queue/                 streams.py, state_store.py, keys.py
│   ├── storage/               models.py, repos.py, engine.py, migrations/, alembic.ini
│   ├── jssandbox/             miniracer.py, sidecar.py, cache.py, sidecar_node/
│   ├── weights/               s3_registry.py, loaders.py, meta.py
│   ├── proxy/                 pool.py, providers.py, proxy.py
│   ├── profiles/              store.py, bundles.py
│   └── pingback.py            2captcha callbackUrl sender
├── engines/                   реверс payload-ов: один каталог на движок
│   ├── base.py                BuildContext, SensorBuilder
│   ├── registry.py            engine → builder
│   ├── __init__.py            импорт всех билдеров, регистрация, build_* хелперы
│   └── {akamai,cloudflare,datadome,kasada,perimeterx,imperva,castle,tspd,cybersiara,twitch,botguard,botdeflector}/builder.py
├── solvers/                   плагины по семействам
│   ├── base.py                Solver ABC, SolverContext, SolverInfo
│   ├── registry.py            регистрация + discover_solvers
│   ├── harness.py             прогон фикстур
│   ├── pow/                   friendly.py, altcha.py
│   ├── ocr/                   text_captcha.py, math_image.py, image_to_text.py
│   ├── audio/                 audio_captcha.py
│   ├── classification/        hcaptcha, recaptcha_grid, funcaptcha_grid, awswaf, shein + backbone/head
│   ├── slider/                slider.py, gap_detect.py, trajectory.py
│   ├── rotate/                rotate.py, regressor.py, classical.py
│   ├── detection/             click, bbox, draw, drag + _yolo
│   ├── score_based/           recaptcha_v3.py, turnstile.py
│   ├── recaptcha/             v2_audio.py
│   ├── funcaptcha/            funcaptcha.py, bda.py
│   ├── antibot/               {cloudflare,akamai,datadome,kasada,perimeterx,imperva,castle,tspd,cybersiara,twitch_integrity,botguard,botdeflector}/
│   └── interactive/           {mtcaptcha,prosopo,captchafox,vk,yandex,temu,binance,hunt,basilisk,alibaba,tencent,yidun,cutcaptcha,atbcaptcha}/
├── crypto/                    aes, rsa, xor, hmac, compress — листовые утилиты
├── behavior/                  bezier, timing, events — листовые утилиты
├── observability/             logging, metrics, tracing, sentry
└── services/                  процессы, только композиция
    ├── gateway/               app.py, cli.py, deps.py, schemas.py,
    │                          routes/{tasks,legacy,report,balance,health,admin}.py
    ├── worker_http/           cli.py, worker.py, context.py, concurrency.py
    ├── worker_cv/             cli.py, worker.py
    ├── worker_protocol/       cli.py, worker.py
    └── cli.py                 операторский CLI: миграции, аккаунты
```

## Правила

1. `domain/` не импортирует ничего, кроме stdlib. Ни redis, ни fastapi, ни structlog.
2. Композиция — только в `services/*/cli.py` и `services/*/app.py`. Внутри домена и
   солверов нет `get_settings()`, нет синглтонов на импорте.
3. Потолок размера файла: солвер 200 строк, адаптер 250, остальное 400.
4. Имя модуля = имя вещи. Запрещены `common`, `utils`, `helpers`, `base` (кроме
   `solvers/base.py` и `engines/base.py`, где это действительно базовый класс слоя).
5. Ошибки переводятся на границе адаптера: curl_cffi → `ProxyError`, redis → `ConfigError`,
   домен и солверы видят только `domain/errors.py`.
6. Один тип капчи — один файл солвера. Если два типа делят >70% кода (GeeTest v3/v4,
   слайдеры одного движка) — один файл с параметром типа.
7. Солвер получает зависимости через `SolverContext`, а не лезет в адаптеры сам.
8. Три лейна и три воркера: `http` (I/O), `cv` (GPU), `protocol` (CPU). Лейн задаётся
   типом капчи в `domain/catalog.py`, клиент на него не влияет.

## Как идёт одна задача

```
POST /createTask                (или legacy POST /in.php)
  services/gateway/routes/      разбор тела (schemas.py), вызов хендлера
  domain/pricing.py             цена по типу
  adapters/storage/repos.py     списание + строка задачи
  adapters/queue/state_store.py pending-хэш в redis (TTL 24ч)
  application/route_task.py     лейн
  adapters/queue/streams.py     XADD в стрим лейна

worker_http | worker_cv | worker_protocol
  XREADGROUP, семафор, таймаут из deadline_ts
  solvers/registry.py           выбор солвера по типу
  solvers/<family>/...          solve_http(task, ctx)
  adapters/queue/state_store.py ready / failed, DLQ по кодам ошибок
  adapters/pingback.py          POST на callbackUrl, если клиент его дал

GET|POST /getTaskResult         (или legacy POST /res.php)
  adapters/queue/state_store.py хэш; нет — adapters/storage/repos.py
  domain/solution.py            проекция результата в ключи 2captcha
```

## Регистрация солверов

Явная, без entry points: `solvers/loader.py` держит кортеж `SOLVERS` из 58 записей вида
`"captcha.solvers.pow.friendly:FriendlySolver"`. Загрузчик импортирует модуль, берёт класс и
регистрирует в реестре; `register()` идемпотентен, поэтому `@register_solver` в модуле
не конфликтует с таблицей.

Пропуск модуля не фатален: таблица обходится по одному солверу, `ImportError` (нет cv2 или
torch) логируется, и солвер просто не появляется у этого воркера. Регистрация без
`--extra cv`: 56 из 58 (падают `slider` и `rotate`). Покрытие типов: 60 из 62 без
экстры, 62 из 62 с ней. Если солвера нет, gateway отбивает тип на входе
(`ERROR_TASK_NOT_SUPPORTED`) и не списывает деньги.

## Что ещё не сделано

1. Runbook на каждый тип (`runbooks/<type>.md`). Пока есть только `_template.md`, а код
   уже ссылается на `runbooks/cloudflare.md` и соседей.
2. Антибот: 12 движков — каркас. Солверы и билдеры кончаются `not_implemented` до живого
   реверса; схемы полей помечены `# FILL:`.
3. ML: код классификации/детекции/поворота написан, весов в реестре нет. Что учить и как
   положить в бакет — `docs/ml.md`, `make expected-models`, `make publish-model`.
4. `domain/ports.py`: `Protocol`-описания `Transport`, `TaskQueue`, `TaskStore`,
   `TaskRepo`, `JsRuntime`, `ProxyPool`, `ModelStore`, `SensorBuilder`; сейчас типы
   границ живут только в сигнатурах.
5. `SolverResult` → варианты (`TokenSolution`, `CookieSolution`, `TextSolution`,
   `GridSolution`, `GeometrySolution`): сейчас это один struct с семью опциональными полями.
6. `make typecheck` красный: ~27 ошибок в queue/streams, state_store, transport и трёх
   солверах. В CI mypy не входит.

## Что уже проверяется

- `make imports` — шесть контрактов `import-linter`: домен чист, `application` не знает
  адаптеров, адаптеры не лезут вверх, `engines`/солверы не трогают адаптеры, листовые
  библиотеки остаются листовыми. Одно исключение задокументировано в контракте (два
  текстовых солвера читают `Settings` как fallback).
- `make lint` — ruff чист.
- `make test` — 262 теста зелёные, 3 тяжёлых пропущены без `--extra cv`.
- CI (`.github/workflows/ci.yml`) гоняет lint, контракты и тесты на Python 3.14.
