# Архитектура captcha-solver — предложение

Черновик для обсуждения. Ничего не реализовано, это карта, по которой будем спорить
и потом резать. Правь прямо в этом файле.

---

## 1. Идея в трёх строках

Тонкое закрытое ядро (типы, состояния, цена, форма ответа) + каталог адаптеров к чужим
системам (62 типа, 12 движков) + процессы-обёртки, которые только собирают зависимости.
Всё, что может сломать чужой релиз, лежит в `engines/` и `solvers/` и меняется локально.

## 2. Правила, на которых держится дерево

1. Зависимости идут в одну сторону: `services → application → domain`, `adapters → domain.ports`,
   `solvers/engines → domain`. Обратных импортов нет.
2. `domain/` не импортирует ничего, кроме stdlib. Ни redis, ни fastapi, ни structlog.
3. Каждый модуль — одна работа. Потолок: солвер 200 строк, адаптер 250, остальное 400.
4. Имя модуля = имя вещи. Запрещены `common`, `utils`, `helpers`, `base`, `misc`.
5. Композиция только в `services/*/wiring.py`. Внутри домена и солверов нет `get_settings()`.
6. Ошибки переводятся на границе адаптера. Домен и солверы видят только `domain/errors.py`.
7. Солвер не знает, какой транспорт и какая очередь под ним. Он получает порты.

## 3. Дерево

```
D:\captcha\
├── ARCHITECTURE.md                  этот файл
├── README.md
├── pyproject.toml                   одна дистрибуция, extras: [gateway] [cv] [browser] [dev]
├── uv.lock
├── Makefile                         sync / lint / fmt / test / dev / canary
├── .env.example
├── docker-compose.yml
├── docker-compose.dev.yml
│
├── src/
│   └── captcha/
│       ├── __init__.py
│       │
│       ├── domain/                  # ЧИСТОЕ ЯДРО. только stdlib
│       │   ├── types.py             # CaptchaType (62), SolveMode, Priority, TaskStatus
│       │   ├── catalog.py           # TypeSpec на каждый тип: family, lane, price, solution, needs_*
│       │   ├── task.py              # Task, TaskEnvelope
│       │   ├── solution.py          # варианты Solution + проекция в 2captcha-ключи
│       │   ├── errors.py            # CaptchaError и подтипы, коды ERR_*
│       │   ├── pricing.py           # правила цены (сама цена — в catalog)
│       │   └── ports.py             # Protocol: Transport, TaskQueue, TaskStore, TaskRepo,
│       │                            #   JsRuntime, ProxyPool, ModelStore, SensorBuilder
│       │
│       ├── application/             # сценарии. Знает порты, не знает технологий
│       │   ├── submit_task.py       # createTask: каталог → цена → списание → строка → очередь
│       │   ├── poll_task.py         # getTaskResult: state-store → репозиторий → проекция
│       │   ├── run_task.py          # воркер: солвер → таймаут → ошибка → запись результата
│       │   ├── pick_solver.py       # выбор солвера по типу и режиму
│       │   └── retry_policy.py      # requeue / DLQ / terminal, коды ошибок
│       │
│       ├── adapters/                # реализации портов, по папке на технологию
│       │   ├── transport/
│       │   │   ├── curl.py          # Client на curl_cffi, сессия на задачу
│       │   │   ├── ja_profiles.py   # таблица JA3/JA4 + impersonate
│       │   │   ├── retry.py         # транспортные ретраи
│       │   │   └── response.py      # обёртка ответа (status, headers, body, elapsed)
│       │   ├── queue/
│       │   │   ├── streams.py       # Producer / Consumer на Redis Streams
│       │   │   ├── keys.py          # имена стримов, групп, DLQ
│       │   │   └── state_store.py   # хэш задачи в redis, TTL 24ч
│       │   ├── storage/
│       │   │   ├── engine.py        # async-движок и фабрика сессий
│       │   │   ├── models.py        # Account, Task, TaskLog, SolverRun, SensorSchemaVersion
│       │   │   ├── repos.py         # AccountRepo, TaskRepo (списание/возврат/статусы)
│       │   │   └── migrations/      # alembic: env.py, versions/0001_init.py
│       │   ├── jssandbox/
│       │   │   ├── miniracer.py     # py-mini-racer, async через to_thread
│       │   │   ├── sidecar.py       # клиент к node-сайдкару
│       │   │   └── cache.py         # кэш скомпилированных скриптов
│       │   ├── weights/
│       │   │   ├── s3_registry.py   # latest-указатель, sha256-кэш, watcher
│       │   │   └── loaders.py       # torch / onnx / hf / safetensors
│       │   ├── proxy/
│       │   │   ├── pool.py          # sticky-сессии, cooldown, mark_bad
│       │   │   └── providers.py     # URL-билдеры BrightData/Oxylabs/Smartproxy/IPRoyal
│       │   ├── profiles/
│       │   │   ├── store.py         # файловое хранилище профилей (+S3 позже)
│       │   │   └── bundles.py       # дефолтные профили (scaffold, заменить на живые)
│       │   └── browser/
│       │       ├── pool.py          # пул контекстов, ротация по max_uses
│       │       ├── session.py       # stealth-переходы, humanize-клик
│       │       └── launchers.py     # camoufox / patchright / playwright
│       │
│       ├── engines/                 # реверс по движкам: схема + сборка + дампы
│       │   ├── __init__.py          # ENGINE_BUILDERS: явный словарь engine → builder
│       │   ├── base.py              # BuildContext, SensorBuilder (реализует ports.SensorBuilder)
│       │   ├── cloudflare/          # schema.py, builder.py, shim.py, fixtures/
│       │   ├── akamai/              # schema.py, builder.py, encoder.py, fixtures/
│       │   ├── datadome/            # schema.py, builder.py, fixtures/
│       │   ├── kasada/              # schema.py, builder.py, pow.py, fixtures/
│       │   ├── perimeterx/          # schema.py, builder.py, fixtures/
│       │   ├── imperva/             # schema.py, builder.py, fixtures/
│       │   ├── castle/              # schema.py, builder.py, fixtures/
│       │   ├── tspd/                # schema.py, builder.py, fixtures/
│       │   ├── cybersiara/          # schema.py, builder.py, fixtures/
│       │   ├── twitch/              # schema.py, builder.py, wasm.py, fixtures/
│       │   ├── botguard/            # schema.py, builder.py, vm.py, fixtures/
│       │   └── botdeflector/        # schema.py, builder.py, fixtures/
│       │
│       ├── solvers/                 # плагины по семействам
│       │   ├── __init__.py          # явные импорты всех солверов (одна строка на файл)
│       │   ├── registry.py          # register() / by_type() / all()
│       │   ├── base.py              # Solver ABC + SolveDeps
│       │   ├── pow/                 # friendly.py, altcha.py
│       │   ├── ocr/                 # text.py, math.py, image.py
│       │   ├── audio/               # whisper.py
│       │   ├── classification/      # grid.py, awswaf.py, shein.py
│       │   ├── slider/              # geetest.py, datadome.py, capy.py, lemin.py,
│       │   │                        # keycaptcha.py, cutcaptcha.py, atb.py, tencent.py,
│       │   │                        # yidun.py, alibaba.py, vk.py, yandex.py, temu.py,
│       │   │                        # binance.py, hunt.py, basilisk.py
│       │   ├── rotate/              # rotate.py, funcaptcha_rotate.py
│       │   ├── detection/           # click.py, bbox.py, draw.py, drag.py
│       │   ├── recaptcha/           # v2.py, v3.py
│       │   ├── funcaptcha/          # token.py, bda.py
│       │   ├── turnstile/           # token.py, invisible.py
│       │   ├── antibot/             # cloudflare.py, akamai.py, datadome.py, kasada.py,
│       │   │                        # perimeterx.py, imperva.py, castle.py, tspd.py,
│       │   │                        # cybersiara.py, twitch.py, botguard.py, botdeflector.py
│       │   └── interactive/         # mtcaptcha.py, prosopo.py, captchafox.py
│       │
│       ├── crypto/                  # листовые утилиты: aes.py, rsa.py, xor.py, hmac.py,
│       │                            #   deflate.py, pow.py (sha256-nonce для Friendly/Altcha)
│       ├── behavior/                # листовые утилиты: bezier.py, timing.py, events.py
│       ├── observability/           # logging.py, metrics.py, tracing.py, sentry.py
│       │
│       └── services/                # только композиция и транспорт
│           ├── gateway/
│           │   ├── __main__.py      # python -m captcha.services.gateway
│           │   ├── app.py           # FastAPI-фабрика, lifespan
│           │   ├── wiring.py        # сборка зависимостей
│           │   ├── errors.py        # доменные коды → 2captcha-конверт
│           │   └── routes/          # tasks.py, balance.py, health.py, admin.py
│           ├── worker_http/
│           │   ├── __main__.py
│           │   ├── wiring.py        # транспорт, js, proxy, профили, солверы
│           │   ├── loop.py          # XREADGROUP + семафор
│           │   ├── concurrency.py
│           │   └── stats.py         # rolling success rate по типу
│           ├── worker_cv/           # __main__.py, wiring.py (weights + gpu)
│           ├── worker_protocol/     # __main__.py, wiring.py (pow/bda/sensors, без сети)
│           ├── worker_browser/      # __main__.py, wiring.py (browser pool)
│           ├── canary/              # __main__.py, suite.py, scheduler.py, alerts.py
│           └── admin/               # __main__.py, app.py, templates/, static/ (опционально)
│
├── tests/                           # зеркало src/captcha, без фикстур движков
│   ├── domain/                      # test_solution.py, test_catalog.py, test_pricing.py
│   ├── application/                 # test_submit_task.py, test_poll_task.py, test_retry_policy.py
│   ├── adapters/                    # test_queue.py, test_repos.py, test_transport.py
│   ├── engines/                     # по одному replay-тесту на движок
│   ├── solvers/                     # по одному тесту на семейство
│   └── services/                    # test_gateway_api.py (httpx + respx)
│
├── fixtures/                        # живые дампы, версионированные по дате
│   ├── cloudflare/2026-11-14/challenge.html, submit.json
│   ├── akamai/2026-11-14/abck.txt, sensor_expected.b64
│   └── ...
│
├── runbooks/                        # по файлу на тип/движок: как регенерить, где дампы
│   ├── cloudflare_challenge.md
│   ├── akamai.md
│   └── ...
│
├── tools/                           # вне прод-пути
│   ├── dumper/                      # снять challenge с живого сайта в fixtures/
│   ├── replayer/                    # прогнать builder против дампа
│   └── labeler/                     # разметка тайлов для CV-датасетов
│
└── infra/
    ├── docker/                      # gateway.Dockerfile, worker-http.Dockerfile, worker-cv.Dockerfile
    ├── compose/                     # dev.yml, test.yml
    └── k8s/                         # helm-чарты (позже)
```

## 4. Каталог типов вместо шести switch-ей

Сейчас добавление типа требует правок в enum, pricing, dispatch, router, solution и новом
солвере — пять мест. Должно быть два: строка в каталоге и папка солвера.

```python
# src/captcha/domain/catalog.py
class SolutionKind(StrEnum):
    GOOGLE_TOKEN = "google_token"     # gRecaptchaResponse
    TOKEN = "token"
    TEXT = "text"
    GEETEST_V3 = "geetest3"
    GEETEST_V4 = "geetest4"
    COOKIES = "cookies"
    NESTED_DATA = "nested"
    GRID = "grid"
    COORDINATES = "coordinates"
    ANGLE = "angle"
    OFFSET = "offset"
    BBOX = "bbox"

@dataclass(frozen=True, slots=True)
class TypeSpec:
    family: str            # папка солвера
    lane: str              # http | cv | protocol | browser
    price: Decimal
    solution: SolutionKind
    needs_js: bool = False
    needs_gpu: bool = False

TYPE_CATALOG: dict[CaptchaType, TypeSpec] = {
    CaptchaType.RECAPTCHA_V2:  TypeSpec("recaptcha",  "http", D("0.0020"), SolutionKind.GOOGLE_TOKEN),
    CaptchaType.CF_CHALLENGE:  TypeSpec("cloudflare", "http", D("0.0030"), SolutionKind.COOKIES, needs_js=True),
    CaptchaType.AKAMAI_SENSOR: TypeSpec("akamai",     "http", D("0.0040"), SolutionKind.COOKIES),
    CaptchaType.GEETEST_V4:    TypeSpec("slider",     "http", D("0.0020"), SolutionKind.GEETEST_V4),
    CaptchaType.CLASSIFICATION_GRID: TypeSpec("classification", "cv", D("0.0010"), SolutionKind.GRID, needs_gpu=True),
    CaptchaType.SLIDER:        TypeSpec("slider",     "cv",   D("0.0008"), SolutionKind.OFFSET, needs_gpu=True),
    ...
}
```

Отсюда без дублирования берут: `pick_lane`, `estimate_cost`, `build_solution`,
`/admin/solvers`, выбор лейна в каталоге типов и валидация "тип вообще поддерживаем".

## 5. Как идёт одна задача

```
POST /createTask
  routes/tasks.py            распарсил тело, вызвал use-case
  application/submit_task.py проверил тип по каталогу, взял цену
                             → ports.TaskRepo.charge(...)         списание в БД
                             → ports.TaskRepo.create(...)         строка задачи
                             → ports.TaskStore.set_pending(env)   хэш в redis
                             → ports.TaskQueue.publish(env, lane) XADD в стрим
  return {"taskId": ...}

воркер
  services/worker_http/loop.py        XREADGROUP, семафор
  application/run_task.py             pick_solver → solve_http → таймаут
                                      на ошибке: retry_policy решает requeue/DLQ/terminal
                                      на успехе: ports.TaskStore.set_ready(result)
                                      
GET /getTaskResult
  application/poll_task.py    TaskStore.get() → нет? TaskRepo.get()
                              → domain/solution.build_solution(type, result)
  return {"status": "ready", "solution": {...}}
```

## 6. Кто что может импортировать

| слой | можно | нельзя |
|---|---|---|
| `domain` | stdlib | всё остальное |
| `application` | `domain` | `adapters`, `services`, технологии |
| `adapters` | `domain` | `application`, `services`, `solvers` |
| `engines` | `domain`, `crypto`, `behavior` | `adapters`    |
| `solvers` | `domain`, `crypto`, `behavior`, `engines` | `adapters`, `services` |
| `services` | всё | — |

Проверяется `import-linter` (контракты `layers` + `forbidden`), падает в CI.

## 7. Что во что превращается

| сейчас | станет |
|---|---|
| `packages/common` | `domain/` (types, task, errors) + `domain/catalog.py` |
| `packages/http` | `adapters/transport/` |
| `packages/queue` | `adapters/queue/` + `application/retry_policy.py` |
| `packages/db` | `adapters/storage/` |
| `packages/js_runtime` | `adapters/jssandbox/` |
| `packages/models` | `adapters/weights/` |
| `packages/proxy` | `adapters/proxy/` |
| `packages/fingerprint` | `adapters/profiles/` + профиль в `domain` |
| `packages/browser` | `adapters/browser/` |
| `packages/sensors` | `engines/` |
| `packages/crypto`, `behavior` | `crypto/`, `behavior/` (как есть) |
| `solvers/_base` | `solvers/base.py` + `solvers/registry.py` |
| `services/gateway` | `services/gateway/` + `application/submit_task.py` + `poll_task.py` |
| `services/worker-http` | `services/worker_http/` + `application/run_task.py` |
| `services/gateway/solution.py` | `domain/solution.py` |
| `services/gateway/pricing.py` | `domain/catalog.py` (цены уехали в данные) |

## 8. Открытые вопросы (то, что надо решить)

1. **Одна дистрибуция против uv-workspace.** Предлагаю одну: один `uv sync`, один mypy,
   один набор extras. Workspace оправдан, когда пакеты версионируются независимо.
2. **`engines/` как верхняя папка против `solvers/antibot/engines/`.** Предлагаю верхнюю:
   Cloudflare-схема нужна и turnstile-солверу, и antibot-солверу, и canary-инструментам.
3. **Один файл на тип против файла на движок.** Предлагаю: тип = файл, но если два типа
   делят >70% кода (GEETEST_V3/V4, слайдеры одного движка) — один файл на движок с
   параметром типа. Иначе получим 14 почти одинаковых файлов.
4. **`Solution` как варианты против одного struct с опциями.** Предлагаю варианты
   (`TokenSolution`, `CookieSolution`, `TextSolution`, `GridSolution`, `GeometrySolution`):
   солвер физически не сможет вернуть «токен и угол», а проекция станет полной.
5. **Где живут дампы.** Предлагаю: короткие сэмплы, на которых гоняются тесты —
   в `engines/<engine>/fixtures/` (рядом с кодом, который они проверяют); большие и
   версионированные по датам — в `fixtures/<engine>/<date>/` и в .gitignore/S3.

## 9. Порядок переезда

1. `git init`, один механический коммит: свернуть пакеты в `src/captcha/`, поправить импорты.
2. `domain/ports.py` + инъекция через конструктор; `SolverContext` → `SolveDeps`.
3. `catalog.py`: цены и лейны уезжают в данные, switch-и уходят.
4. `Solution`-варианты + `build_solution` по `SolutionKind`.
5. `import-linter` в CI, mypy strict на `domain` и `application`.
6. Первый солвер (`solvers/pow/friendly.py`) как эталон для остальных.
