# Архитектура

Одна дистрибуция `captcha`, слои с односторонними зависимостями, плагины солверов по
семействам, реверс движков отдельно от солверов.

## Принцип

Ядро — то, что решаем мы и что не может сломать чужой релиз: типы капч, состояния задачи,
цены, коды ошибок, форма решения. Адаптация — то, чья правильность задана чужой спекой:
62 типа, 12 антибот-движков, их payload-схемы. Ядро должно быть маленьким и закрытым,
адаптация — локальной: правка в одном движке не должна тянуть за собой остальные.

## Слои

| слой | что внутри | можно импортировать | нельзя |
|---|---|---|---|
| `domain/` | типы, состояния, ошибки, цены, форма решения, профиль | stdlib | всё остальное |
| `application/` | сценарии: маршрутизация задачи | `domain` | `adapters`, `services` |
| `adapters/` | redis, postgres, curl, v8, s3, playwright | `domain` | `application`, `solvers` |
| `engines/` | сборка payload-ов по движкам | `domain`, `crypto`, `behavior` | `adapters` |
| `solvers/` | плагины по семействам | `domain`, `crypto`, `behavior`, `engines` | `adapters`, `services` |
| `services/` | процессы: композиция + транспорт | всё | — |

Направление ровно такое: `services → application → domain`, а адаптеры реализуют порты
домена. Проверяется `import-linter` (см. `pyproject.toml` → контракты добавляются в CI).

## Дерево

```
src/captcha/
├── config.py                  Settings (pydantic-settings), читается только в services
├── domain/                    ЧИСТОЕ ЯДРО, без I/O
│   ├── enums.py               CaptchaType (62), AntibotEngine (12), SolveMode, TaskStatus, Priority
│   ├── task.py                Task, TaskEnvelope, SolverResult, new_task_id
│   ├── solution.py            проекция SolverResult → ключи 2captcha по типу
│   ├── errors.py              CaptchaError и подтипы, коды ERR_*
│   ├── pricing.py             цены по типам
│   ├── lanes.py               Lane и pick_lane (какой воркер берёт задачу)
│   └── profile.py             Profile — структура фингерпринта
├── application/
│   └── route_task.py          HTTP-first выбор лейна на входе в очередь
├── adapters/
│   ├── transport/             curl.py, ja_profiles.py, retry.py, response.py
│   ├── queue/                 streams.py, state_store.py, keys.py
│   ├── storage/               models.py, repos.py, engine.py, migrations/, alembic.ini
│   ├── jssandbox/             miniracer.py, sidecar.py, cache.py, sidecar_node/
│   ├── weights/               s3_registry.py, loaders.py, meta.py
│   ├── proxy/                 pool.py, providers.py, proxy.py
│   ├── profiles/              store.py, bundles.py
│   └── browser/               pool.py, session.py, launchers.py
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
    ├── gateway/               app.py, cli.py, wiring через deps.py, routes/{tasks,balance,health,admin}.py, schemas.py
    ├── worker_http/           cli.py, worker.py, context.py, concurrency.py, stats.py
    ├── worker_cv/             cli.py, worker.py
    ├── worker_protocol/       cli.py, worker.py
    ├── worker_browser/        cli.py, worker.py
    ├── canary/                cli.py, suite.py, scheduler.py, alerts.py
    ├── admin/                 cli.py, app.py, templates/, static/
    └── pipeline/              cli.py, sampler.py, retrain.py, drift.py, s3client.py
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

## Как идёт одна задача

```
POST /createTask
  services/gateway/routes/tasks.py   разбор тела (schemas.py), вызов хендлера
  domain/pricing.py                  цена по типу
  adapters/storage/repos.py          списание + строка задачи
  adapters/queue/state_store.py      pending-хэш в redis (TTL 24ч)
  application/route_task.py          лейн
  adapters/queue/streams.py          XADD в стрим лейна

worker_http
  services/worker_http/worker.py     XREADGROUP, семафор, таймаут из deadline_ts
  solvers/registry.py                выбор солвера по типу
  solvers/<family>/...               solve_http(task, ctx)
  adapters/queue/state_store.py      ready / failed, DLQ по кодам ошибок

GET /getTaskResult
  adapters/queue/state_store.py      хэш; нет — adapters/storage/repos.py
  domain/solution.py                 проекция результата в ключи 2captcha
```

## Что ещё не сделано (следующие шаги)

1. `domain/catalog.py`: таблица `CaptchaType → TypeSpec(family, lane, price, solution_kind)`,
   чтобы `pricing.py`, `lanes.py` и `solution.py` перестали держать свои списки, а новый
   тип добавлялся строкой данных, а не правками в четырёх местах.
2. `domain/ports.py`: `Protocol`-описания `Transport`, `TaskQueue`, `TaskStore`, `TaskRepo`,
   `JsRuntime`, `ProxyPool`, `ModelStore`, `SensorBuilder`; сейчас типы границ живут
   только в сигнатурах.
3. `SolverResult` → варианты (`TokenSolution`, `CookieSolution`, `TextSolution`,
   `GridSolution`, `GeometrySolution`): сейчас это один struct с семью опциональными полями.
## Регистрация солверов

Явная, без entry points: `solvers/loader.py` держит кортеж `SOLVERS` из 53 записей вида
`"captcha.solvers.pow.friendly:FriendlySolver"`. Загрузчик импортирует модуль, берёт класс и
регистрирует в реестре; `register()` идемпотентен, поэтому `@register_solver` в модуле
не конфликтует с таблицей.

Пропуск модуля не фатален: таблица обходится по одному солверу, `ImportError` (нет cv2 или
torch) логируется и солвер просто не появляется у этого воркера. Регистрация без
`--extra cv`: 51 из 53 (падают `slider` и `rotate`). Без солвера тип получает
`ERR_NO_SOLVER`, а не падение процесса.

## Что уже проверяется

- `make imports` — шесть контрактов `import-linter` в `pyproject.toml`: домен чист,
  `application` не знает адаптеров, адаптеры не лезут вверх, `engines`/солверы не трогают
  адаптеры, листовые библиотеки остаются листовыми. Одно исключение задокументировано
  в контракте (два текстовых солвера читают `Settings` как fallback).
- `make lint` — ruff чист (305 автофиксов долга уже применены, форматирование гоняется).
- `make test` — 40 тестов зелёные, 3 тяжёлых пропущены без `--extra cv`.
- CI (`.github/workflows/ci.yml`) гоняет всё это на Python 3.14.
