# Архитектура

Одна дистрибуция `captcha`: слои с односторонними зависимостями, четыре лейна,
плагины солверов по семействам, реверс движков отдельно от солверов.

## Принцип

Ядро — то, что решаем мы и что не может сломать чужой релиз: типы капч, состояния задачи,
цены, коды ошибок, форма решения. Адаптация — то, чья правильность задана чужой спекой:
62 типа, 12 антибот-движков, их payload-схемы. Ядро должно быть маленьким и закрытым,
адаптация — локальной: правка в одном движке не должна тянуть за собой остальные.

**HTTP-first.** Всё, что решается по HTTP, решается по HTTP: `curl_cffi` даёт TLS-отпечаток,
`adapters/jssandbox` исполняет obfuscated challenge, сенсоры собираются в `engines/`.
Браузер — второй контур, а не первый: он стоит ~700 МБ RAM на задачу и 1–3 с на запуск,
поэтому включается только там, где HTTP-путь упёрся в отпечаток или в страницу, которую
клиент не может собрать сам.

**Где HTTP не проходит.** Замер живым probe (`tools/hcaptcha_probe.py`, снимок
2026-11-15, [hcaptcha-sites.md](hcaptcha-sites.md)): Steam (`store.steampowered.com`,
`steamcommunity.com`) и Riot (`authenticate.riotgames.com`, `account.riotgames.com`) на
одном egress IP дали 0 pass из 10 — сервер возвращает новый `c` вместо токена;
контрольный passive-ключ hCaptcha — 10 из 10. Это и есть граница HTTP-first: отпечаток
Riot/Steam по HTTP не собирается, поэтому hCaptcha-family живёт в браузерном лейне, а
«доля escalations» — не 1–2%, а существенная часть hCaptcha-трафика.

**Один прыжок.** HTTP-солвер, которому нужен браузер, не запускает его сам и не падает:
он бросает `NeedsBrowserError`, и воркер перекладывает конверт в лейн `browser`.
`TaskEnvelope.lane_hops` ограничен единицей (`domain/task.py: MAX_LANE_HOPS = 1`):
второй запрос браузера — терминальный `ERR_UNSOLVABLE`, а не цикл.

## Четыре лейна

| лейн | воркер | что внутри | типов | ключевые настройки |
|---|---|---|---|---|
| `http` | `services/worker_http` | `curl_cffi` + JA3/JA4-профили, JS-песочница, сенсор-билдеры | 21 | `WORKER_HTTP_CONCURRENCY=512`, `WORKER_HTTP_IMPERSONATE=chrome124` |
| `cv` | `services/worker_cv` | torch/torchvision/opencv/whisper, реестр весов | 34 (+ `hopscotch`) | `WORKER_CV_DEVICE=cpu` (на GPU-ноде — `cuda`) |
| `protocol` | `services/worker_protocol` | PoW, сборка сенсоров, чистый CPU | 2 | `WORKER_PROTOCOL_CONSUMERS=2` |
| `browser` | `services/worker_browser` | Camoufox, одна свежая сессия на задачу | 5 | `WORKER_BROWSER_SESSIONS=12`, `WORKER_BROWSER_MIN_FREE_MB=2048`, `WORKER_BROWSER_TASK_TIMEOUT_SEC=240` |

Раскладка по `domain/catalog.py`, проверяется одной командой:

~~~powershell
$env:PYTHONPATH='src'
uv run python -c "from captcha.domain.enums import CaptchaType; from captcha.domain import catalog; print({l: len([t for t in CaptchaType if catalog.lane_for(t) == l]) for l in catalog.LANES})"
# {'http': 21, 'cv': 35, 'protocol': 2, 'browser': 5}    (cv = 34 вендорских + hopscotch)
~~~

Лейн — свойство типа, не клиента: `application/route_task.py` читает каталог и кладёт
конверт в `captcha:stream:<lane>`. Исключение — внутренний `Task.preferred_mode`
(`SolveMode.BROWSER`/`HTTP`), который выставляет gateway или профиль сайта: им
hCaptcha-family уходит в браузер, а тип, у которого лейн по умолчанию `browser`, может
быть отправлен в HTTP для probe. Клиент этим полем не управляет.

## Слои

| слой | что внутри | можно импортировать | нельзя |
|---|---|---|---|
| `domain/` | типы, состояния, ошибки, цены, форма решения, профиль, порты, redaction, apikey | stdlib (+msgspec) | всё остальное |
| `application/` | сценарии: маршрутизация задачи, пингбэк, платежи, usage | `domain` | `adapters`, `services` |
| `adapters/` | redis, postgres, curl, v8, s3, прокси, профили, браузер, netguard | `domain` | `application`, `solvers` |
| `engines/` | сборка payload-ов по движкам | `domain`, `crypto`, `behavior` | `adapters` |
| `solvers/` | плагины по семействам | `domain`, `crypto`, `behavior`, `engines` | `adapters`, `services` |
| `services/` | процессы: композиция + транспорт | всё | — |

Направление: `services → application → domain`, адаптеры реализуют порты домена
(`domain/ports.py`). Проверяется `import-linter`: шесть контрактов в `pyproject.toml`.

## Дерево

~~~
src/captcha/
├── config.py                  Settings (pydantic-settings), читается только в services
├── domain/                    ЧИСТОЕ ЯДРО, без I/O
│   ├── enums.py               CaptchaType (62), AntibotEngine (12), SolveMode, TaskStatus, Priority
│   ├── catalog.py             TypeSpec (lane, price, solution_kind) — одна строка на тип
│   ├── task.py                Task, TaskEnvelope, SolverResult, MAX_LANE_HOPS, new_task_id
│   ├── solution.py            проекция SolverResult → ключи 2captcha (dispatch по solution_kind)
│   ├── errors.py              CaptchaError и подтипы, коды ERR_*
│   ├── lanes.py               Lane и pick_lane (какой воркер берёт задачу)
│   ├── ports.py               Protocol-описания границ: Transport, BrowserProvider, BrowserSpec…
│   ├── redaction.py           вырезание proxy*/cookies из payload и DLQ
│   ├── apikey.py              HMAC-SHA256(pepper, key), key_tail
│   ├── pricing.py             цены по типам (тонкий фасад над catalog)
│   └── profile.py             Profile — структура фингерпринта
├── application/
│   ├── route_task.py          выбор лейна на входе в очередь
│   ├── pingback.py            тело callbackUrl
│   ├── payments.py            провайдеры пополнения
│   └── usage.py               агрегаты кабинета
├── adapters/
│   ├── transport/             curl.py, ja_profiles.py, retry.py, response.py
│   ├── queue/                 streams.py, state_store.py, keys.py, capabilities.py
│   ├── storage/               models.py, repos.py, persistence.py, engine.py, migrations/
│   ├── jssandbox/             miniracer.py, sidecar.py, cache.py, sidecar_node/
│   ├── browser/               provider.py, prefs.py, supervisor.py, geoip.py, proxy.py, pool.py
│   ├── hcaptcha_js/           runner.py + bundle.js (hsw/hsj для hCaptcha HTTP-lane)
│   ├── weights/               s3_registry.py, loaders.py, meta.py, bundle.py, artifact.py
│   ├── proxy/                 pool.py, providers.py, proxy.py
│   ├── profiles/              store.py, bundles.py
│   ├── netguard.py            SSRF-проверка клиентских URL
│   └── pingback.py            2captcha callbackUrl sender
├── engines/                   реверс payload-ов: один каталог на движок
│   ├── base.py                BuildContext, SensorBuilder
│   ├── registry.py            engine → builder
│   └── {akamai,cloudflare,datadome,kasada,perimeterx,imperva,castle,tspd,cybersiara,twitch,botguard,botdeflector}/builder.py
├── solvers/                   плагины по семействам
│   ├── base.py                Solver ABC, SolverContext, SolverInfo
│   ├── registry.py            регистрация + discover_solvers
│   ├── loader.py              таблица SOLVERS
│   ├── harness.py             прогон фикстур
│   ├── hcaptcha/              http.py, browser.py, sites.py, wire.py, common.py
│   ├── pow/                   friendly.py, altcha.py
│   ├── ocr/                   text_captcha.py, math_image.py, image_to_text.py
│   ├── audio/                 audio_captcha.py
│   ├── classification/        hcaptcha, recaptcha_grid, funcaptcha_grid, awswaf, shein + backbone/head
│   ├── slider/                slider.py, gap_detect.py, trajectory.py
│   ├── rotate/                rotate.py, regressor.py, classical.py
│   ├── detection/             click, bbox, draw, drag + _yolo
│   ├── score_based/           recaptcha_v3.py, turnstile.py
│   ├── recaptcha/             v2_audio.py
│   ├── funcaptcha/            funcaptcha.py, browser.py, session.py, variants.py,
│   │                          wire.py, settings.py, bda.py, sensor.py, fields.py, devices.py
│   ├── antibot/               {cloudflare,akamai,datadome,kasada,perimeterx,imperva,castle,tspd,cybersiara,twitch_integrity,botguard,botdeflector}/
│   └── interactive/           {mtcaptcha,prosopo,captchafox,vk,yandex,temu,binance,hunt,basilisk,alibaba,tencent,yidun,cutcaptcha,atbcaptcha}/
├── crypto/                    aes, rsa, xor, hmac, compress — листовые утилиты
├── behavior/                  bezier, timing, events — листовые утилиты
├── observability/             logging, metrics, tracing, sentry
└── services/                  процессы, только композиция
    ├── gateway/               app.py, auth.py, quotas.py, schemas.py, type_map.py,
    │                          routes/{tasks,legacy,report,balance,health,cabinet,admin}.py
    ├── worker_http/           cli.py, worker.py, escalation.py, context.py, concurrency.py
    ├── worker_cv/             cli.py, worker.py
    ├── worker_protocol/       cli.py, worker.py
    ├── worker_browser/        cli.py, worker.py, runner.py, gate.py, heartbeat.py, limits.py, tracking.py
    ├── canary/                cli.py, suite.py, alerts.py, scheduler.py
    ├── pipeline/              sampler.py, golden.py, retrain.py, drift.py
    └── cli.py                 операторский CLI: миграции, аккаунты
~~~

## Правила

1. `domain/` не импортирует ничего, кроме stdlib (и `msgspec` для структур). Ни redis,
   ни fastapi, ни structlog.
2. Композиция — только в `services/*/cli.py` и `services/*/app.py`. Внутри домена и
   солверов нет `get_settings()`, нет синглтонов на импорте.
3. Потолок размера файла: солвер 200 строк, адаптер 250, остальное 400.
4. Имя модуля = имя вещи. Запрещены `common`, `utils`, `helpers`, `base` (кроме
   `solvers/base.py`, `engines/base.py`, `services/gateway/routes/…`, где это действительно
   базовый класс слоя).
5. Ошибки переводятся на границе адаптера: curl_cffi → `ProxyError`, redis → `ConfigError`,
   домен и солверы видят только `domain/errors.py`.
6. Один тип капчи — один файл солвера. Если два типа делят >70% кода (GeeTest v3/v4,
   hCaptcha http/browser, слайдеры одного движка) — один файл с параметром типа.
7. Солвер получает зависимости через `SolverContext`, а не лезет в адаптеры сам. Ни HTTP-клиент,
   ни прокси, ни модель, ни браузер солвер не создаёт.
8. Четыре лейна и четыре воркера: `http` (I/O), `cv` (GPU/CPU-инференс), `protocol`
   (CPU), `browser` (RAM). Лейн задаётся типом капчи в `domain/catalog.py`.
9. Один прыжок между лейнами (`MAX_LANE_HOPS = 1`). Второй `NeedsBrowserError` — терминал.
10. Одна сессия браузера — одна задача. Общего контекста между клиентами нет.

## Как идёт одна задача

~~~
POST /createTask                (или legacy POST /in.php)
  services/gateway/schemas.py   разбор тела, type_map: официальные имена → CaptchaType
  services/gateway/quotas.py    rate limit / active / daily / spend (Redis-счётчики)
  domain/pricing.py             цена по типу
  adapters/storage/repos.py     списание + строка задачи (payload без proxy*/cookies)
  adapters/queue/state_store.py pending-хэш в redis (TTL 24ч)
  application/route_task.py     лейн (catalog + preferred_mode)
  adapters/queue/streams.py     XADD в captcha:stream:<lane>

worker_http | worker_cv | worker_protocol | worker_browser
  XREADGROUP, семафор, таймаут из deadline_ts
  solvers/registry.py           выбор солвера по типу и режиму
  solvers/<family>/...          solve_http(task, ctx) | solve_browser(task, ctx)
  ERR_NEEDS_BROWSER → escalation.py: XADD browser, ack исходного, billing не трогаем
  adapters/queue/state_store.py ready / failed, DLQ по кодам ошибок
  adapters/pingback.py          POST на callbackUrl, если клиент его дал

GET|POST /getTaskResult         (или legacy POST /res.php)
  adapters/queue/state_store.py хэш; нет — adapters/storage/repos.py
  domain/solution.py            проекция результата в ключи 2captcha
~~~

## HTTP-first и NeedsBrowser

`NeedsBrowserError` (`ERR_NEEDS_BROWSER`) — не ошибка задачи, а сигнал «этот отпечаток
по HTTP не собирается». Обработка ровно одна (`services/worker_http/worker.py`):

1. `escalate_to_browser` публикует конверт в `captcha:stream:browser`, увеличив `lane_hops`;
2. исходное сообщение ack-ается только после успешной публикации;
3. статус задачи остаётся `processing`, списание не повторяется и не отменяется;
4. второй запрос браузера из браузерного лейна — `UnsolvableError` (`worker_browser/runner.py`);
5. если publish не удался, задача остаётся в HTTP-лейне и уходит в обычный retry.

Счётчик `captcha_escalations_total{type}` показывает, сколько трафика реально требует
браузера. Это единственный поддерживаемый способ попасть в браузерный лейн: клиент не может
попросить браузер полем `type` или `mode`.

## Browser lane

Провайдер — `adapters/browser/provider.py`, реализация порта `BrowserProvider` из
`domain/ports.py`:

~~~python
@asynccontextmanager
async def session(self, spec: BrowserSpec) -> AsyncIterator[BrowserSession]: ...
~~~

Что гарантирует провайдер:

- **Одна свежая сессия на задачу.** Один запуск Camoufox и один временный профиль
  `<temp>/captcha-browser-*` на задачу; после выхода профиль удаляется, дерево процессов
  добивается (`supervisor.terminate_tree`). Осиротевшие профили сносит `reap_stale` при
  старте процесса и на первом `session()`.
- **GeoIP по exit-IP прокси.** Для задачи с прокси провайдер резолвит exit-IP и передаёт его
  в `geoip`; если IP не резолвится — `ProxyError`, host-fallback запрещён. UA/locale/timezone
  из `BrowserSpec` принимаются, но не применяются: фингерпринт строит Camoufox, ручной
  оверрайд делает его противоречивым.
- **Опции** (`prefs.build_options`): `os="windows"`, `block_webrtc=True`, `enable_cache=False`,
  `persistent_context=True`, `user_data_dir=<temp>`, `firefox_user_prefs=FIREFOX_PREFS`
  (один content-процесс, без телеметрии/кэшей/медиа), `exclude_addons=[DefaultAddons.UBO]`.
  UBO выключен не из вредности: с ним резидентная память ~1.1 ГБ против ~0.55 ГБ на пустой
  странице (`adapters/browser/prefs.py`).

Стоимость и лимиты:

| параметр | значение | источник |
|---|---|---|
| RSS одной активной страницы | ~700 МБ (0.5–1.1 ГБ в зависимости от страницы и addons) | baseline (замер пользователя), `provider.py` |
| сессий по умолчанию | 12 на 16 ГБ ноду | `WORKER_BROWSER_SESSIONS=12` |
| рабочий диапазон тюнинга | 8–20 | контракт §4 |
| свободная RAM, ниже которой новые сессии не стартуют | 2048 МБ | `WORKER_BROWSER_MIN_FREE_MB` |
| hard timeout на задачу | 180–300 с, по умолчанию 240 | `worker_browser/limits.py` |
| ramp | старт с 2 сессий, +1 после 8 чистых задач | `WORKER_BROWSER_RAMP_START`, `gate.py` |

Admission control — `worker_browser/gate.py`: семафор + проверка свободной RAM (Linux
`/proc/meminfo`, Windows `GlobalMemoryStatusEx`) + плавный ramp. Таймаут задачи убивает
дерево процессов по label задачи (`tracking.py` → `supervisor.kill`), а не только отменяет
корутину: иначе content-процесс держит профиль и RAM. Метрики лейна:
`captcha_browser_sessions_active`, `captcha_browser_launch_seconds`, `captcha_browser_rss_bytes`.

## Capabilities и heartbeat

Ключи (контракт §2, `adapters/queue/capabilities.py`):

~~~
SETEX captcha:lane:<lane>:<worker> 90 1      # жив ли воркер
SADD  captcha:cap:<lane> <type>...           # что он умеет
~~~

TTL 90 с, бит раз в 30 с. Битят два лейна: `worker_http` (`worker_http/heartbeat.py`) и
`worker_browser` (`worker_browser/heartbeat.py`); `worker_cv` и `worker_protocol` heartbeat
не пишут. Просроченный heartbeat убирает воркера из маршрута за один TTL; набор типов живёт
дольше, но лейн без живого heartbeat игнорируется целиком, поэтому устаревший набор не может
«воскресить» мёртвый лейн. `/readyz` отвечает 503, если Redis, Postgres или ни одного живого
heartbeat.

Продажу heartbeat ограничивает только для `browser`: `gateway/app.py: _available_types`
отдаёт `(реестр − browser-типы) ∪ live_types`, то есть дорогой лейн продаётся лишь при
свежем heartbeat, а остальные типы — из реестра. Live-набор читается один раз в
`build_state`, поэтому смерть browser-воркера после старта gateway видна только новому
heartbeat-опросу при следующем рестарте.

## Canary

`services/canary` — отдельный процесс, `CANARY_INTERVAL_SEC=21600` (6 ч). Он берёт JSON-фикстуры
из `CANARY_FIXTURES_DIR` (по умолчанию `tests/canary/fixtures`), гоняет их через живой
`/createTask` → `/getTaskResult`, считает pass rate и p50/p95, пишет вердикт по типу в
`captcha:typehealth:<type>` (`ok`/`degraded`, TTL 12 ч) и в метрики
`captcha_canary_pass_rate{type}`, `captcha_type_health{type}`. Ниже порога
`CANARY_PASS_THRESHOLD` (0.9) — POST в `CANARY_ALERT_WEBHOOK`.

Фикстуры в репозитории: `tests/canary/fixtures/` — `friendly_captcha_v1` и `altcha_v1`,
то есть 2 типа из 62; `tests/canary/test_fixtures.py` пинит непустой набор и проверяет, что
payload парсится тем солвером, который его получит. Остальные 60 типов фикстур не имеют.

Пустой набор фикстур — отдельный случай: отчёт получается `pass_rate=1.0`, алерт уходит
только как `reason=no_fixtures` при настроенном webhook, а `--once` возвращает 0. Поэтому
«канарейка зелёная» без фикстур — не результат, а отсутствие измерения. Порядок L0–L3 и
пороги приёмки — `docs/plan.md` §2.

## Визуальное ядро и головы

Решение по CV — [ADR-001](adr-vision-architecture.md), обзор моделей и лицензий —
[ml-research.md](ml-research.md). Кратко:

- hot path — замороженный **DINOv3 ViT-B/16** (768-d, ~0.17 ГБ весов) + маленькие головы;
- SigLIP2-B/16 — второй энкодер и zero-shot text-matching для новых промптов;
- головы: OCR (PaddleOCR PP-OCRv5, fallback TrOCR), grid (линейная/MLP), detection
  (RT-DETR-R18/R34, ONNX), geometry (классика + ResNet50 для rotate), matching
  (DINOv3 patch features + query-голова, как в `hopscotch-v1`);
- VLM (Qwen2.5-VL-7B, 4-bit) — только fallback по калиброванному отказу, на ноде ≥24 ГБ,
  включается `VLM_FALLBACK=1`; без неё — abstention;
- YOLO11 (AGPL-3.0) в прод-путь не идёт; текущий `detection/_yolo.py` помечен как
  юридический риск до перехода на ONNX.

Формат артефактов не меняется: `classifier.weight [C,768]` + `meta.base_backbone`,
safetensors, `.pt`/ONNX. Именно поэтому смена backbone — правка meta и препроцесса, а не
новый контракт. Точность на целевых типах **не измерена**: ни весов в бакете, ни golden-наборов
на момент этого документа нет (см. `docs/baseline.md`, «Known unknowns»).

## Регистрация солверов

Явная, без entry points: `solvers/loader.py` держит кортеж `SOLVERS` из 60 записей вида
`"captcha.solvers.pow.friendly:FriendlySolver"`. Загрузчик импортирует модуль, берёт класс и
регистрирует в реестре; `register()` идемпотентен.

Пропуск модуля не фатален: таблица обходится по одному солверу, `ImportError` логируется, и
солвер просто не появляется у этого воркера. Замер на 2026-11-15 (venv без torch, cv2 и
onnxruntime):

~~~
SOLVERS: 60 записей
загружено без --extra cv: 60 (импорт ни одного модуля не тянет cv-стек)
покрытие типов по реестру: 63 из 63 значений CaptchaType (62 вендорских + hopscotch)
~~~

Импорт ≠ работа: `slider` решает на numpy-бэкенде (`gap_detect.BACKENDS`), а `rotate`,
detection- и classification-солверы импортируются, но отвечают на задаче `ERR_CONFIG` без
`cv2`/торча/весов. `experimental=True` стоит у 42 из 60 солверов — это 38 вендорских типов
плюс `hopscotch`. `docs/baseline.md` снят раньше (56 загруженных, 60/62) и не
переписывается. Если солвера нет, gateway отбивает тип на входе (`ERROR_TASK_NOT_SUPPORTED`)
и не списывает деньги.

## Что ещё не сделано

1. **Продажа по heartbeat покрывает только `browser`.** `_available_types`
   (`services/gateway/app.py`) продаёт типы `browser`-лейна лишь при свежем heartbeat, а
   остальные берёт из реестра. `CAPTCHA_EXTRA_TYPES` (контракт §7) не существует; live-набор
   читается один раз в `build_state`, поэтому упавший после старта browser-воркер не убирает
   свои типы до рестарта gateway; `worker_cv` и `worker_protocol` heartbeat не пишут.
2. **`ALLOW_EXPERIMENTAL_TASKS` реализован, но в beta включён.** `config.py`
   (`allow_experimental_tasks`, default `True`) и `sellable_types` фильтруют
   experimental-only типы только при `0`; `worker_http/worker.py` по-прежнему выбирает
   солвер с `allow_experimental=True`. С `0` из 62 вендорских типов продаются 24 — у
   остальных 38 единственный солвер помечен `experimental`. Контракт §7 требует `0` в
   проде; переменной нет в `.env.example`.
3. **TLS-ингресс есть.** `infra/caddy/Caddyfile` + сервис `caddy` — единственный с
   published ports (80/443): ACME при заданном `CAPTCHA_DOMAIN`, иначе внутренний CA;
   gateway торчит только `expose: 8080`. Дев-оверлей (`docker-compose.dev.yml`) выключает
   Caddy профилем `edge` и публикует `127.0.0.1:8080`. Прод-схема — `runbooks/deploy.md`.
4. **worker-browser и canary в compose, но за профилями** `browser` и `canary`, поэтому
   `make dev` их не поднимает: browser-ноде нужен camoufox и ~16 ГБ RAM. Фикстуры canary
   монтируются read-only из `./tests/canary/fixtures`.
5. **netguard не на границе gateway — в работе.** Модуль и тесты есть
   (`adapters/netguard.py`, `tests/adapters/test_netguard.py`), вызовы — только браузерная
   навигация (`adapters/browser/provider.py`). `pageurl`, `websiteURL`, `challenge_url`,
   `surl`, `api_server` из `createTask` доходят до HTTP-солверов без проверки; ветка
   `feat/netguard-http` не смержена; в `SECURITY.md` §8 это открытый пункт 1.
6. **Runbook на каждый тип.** Есть шаблон, операционные runbook-и и типовые на 36 из
   62 вендорских типов плюс `hopscotch`; остальные 26 заведены в `runbooks/README.md` со
   статусом «pending».
7. **Антибот: wire собран, живого реверса нет.** `not_implemented()` остался только у
   `botdeflector`; остальные 11 движков собирают payload и результат, но схемы полей
   помечены `# FILL:`, живой прогон не подтверждён — отсюда `experimental=True`.
8. **ML: весов нет.** Код классификации/детекции/поворота написан; что учить и как положить
   в бакет — `docs/ml.md`, `make expected-models`, `make publish-model`.
9. **`SolverResult` — один struct** с семью опциональными полями; варианты
   (`TokenSolution`, `CookieSolution`, …) не выделены.
10. **mypy красный целиком**, в CI входит только scoped-набор (`make typecheck-core`:
    domain, application, engines, storage, netguard, metrics — ноль ошибок).