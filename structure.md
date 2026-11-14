# Структура проекта captcha-solver

Monorepo, Python 3.12, uv workspace. HTTP-first архитектура: главный воркер — HTTP, браузер только fallback.

---

## Язык: Python 3.12 (+ опциональный Go-gateway позже)

**Почему pure Python базой:**
- Весь ML-стек тут: PyTorch, transformers, Whisper, YOLO, timm, peft.
- **curl_cffi (curl-impersonate binding)** — ядро HTTP-стелса, Python-first. В Go/Rust либо нет, либо беднее.
- Стелс-тулинг родной в питоне — camoufox, nodriver, patchright, playwright, tls-client.
- Capsolver, 2captcha, capmonster — все питоновские в ядре. Проверенный выбор.
- Одна экосистема = одна команда, один CI, один on-call skillset.

**Где Python больно:**
- Gateway под нагрузкой >5k RPS. FastAPI + uvloop + msgspec тащит до 10k RPS на одной ноде.
- Если RPS выше (20k+) — gateway переписать на Go **позже**. Протокол HTTP, замена атомарная.
- JS-runtime для obfuscated challenge: py-mini-racer работает, но тяжёлые скрипты лучше в Node-сайдкаре через IPC.

**TypeScript** — только для Node-сайдкара JS-runtime, если py-mini-racer не тащит.

**Rust** — точечно, для горячих BDA-payload билдеров и PoW через pyo3, если bottleneck.

---

## Дерево папок

```
captcha-solver/
├── services/                   # запускаемые процессы
│   ├── gateway/                # FastAPI, 2captcha-compat (createTask/getTaskResult/balance), HTTP-first router
│   ├── worker-http/            # ГЛАВНЫЙ воркер: curl_cffi + sensor + JS-runtime. 1000+ concurrent/node
│   ├── worker-cv/              # ML inference (classification, OCR, audio, detection)
│   ├── worker-protocol/        # PoW, BDA, sensor builders — pure CPU
│   ├── worker-browser/         # FALLBACK только: 1-3 ноды, 15-25 сессий/нода
│   ├── js-sidecar/             # (optional) Node-процесс для тяжёлого obfuscated JS
│   ├── canary/                 # регресс-бот, живые кейсы каждые 6ч
│   ├── admin/                  # dashboard backend (FastAPI + htmx, или svelte front отдельно)
│   └── pipeline/               # dataset collection, retrain scheduler, labeling queue
│
├── solvers/                    # плагины, подключаются через registry
│   ├── _base/                  # Task/Result schemas, Solver ABC, test harness, HTTP/browser mode flags
│   ├── ocr/                    # image-to-text, math, text — pure HTTP
│   ├── audio/                  # whisper wrapper — pure HTTP
│   ├── classification/         # ViT backbone + LoRA heads (hCaptcha, reCAPTCHA Grid, FunCaptcha Grid, AWS WAF, Shein) — pure HTTP
│   ├── slider/                 # gap-detect + trajectory → все slider-типы. HTTP: image+offset mode
│   ├── rotate/                 # angle regressor — pure HTTP
│   ├── detection/              # YOLO: click, draw, drag, bbox — pure HTTP
│   ├── pow/                    # friendly, altcha — pure HTTP
│   ├── score_based/            # recaptcha v3, turnstile invisible — HTTP sensor + JA3
│   ├── recaptcha/              # v2 checkbox/invisible/callback через audio-bypass, Enterprise — HTTP
│   ├── funcaptcha/             # Arkose, в т.ч. BDA (JS-runtime), Enterprise, Proxyless
│   ├── antibot/                # HTTP-first, каждый подкаталог — свой sensor-reverse
│   │   ├── cloudflare/         # Challenge (challenge-JS runtime), Turnstile interactive
│   │   ├── akamai/             # sensor_data generator + SBSD
│   │   ├── datadome/           # Slider + device check + ddtk endpoint
│   │   ├── kasada/             # x-kpsdk payload
│   │   ├── perimeterx/         # _px / _pxhd / sensor
│   │   ├── imperva/            # ___utmvc, incap_ses
│   │   ├── castle/             # fingerprint-based HTTP
│   │   ├── tspd/
│   │   ├── cybersiara/
│   │   ├── twitch_integrity/
│   │   ├── botguard/
│   │   └── botdeflector/
│   └── interactive/            # mtcaptcha, tencent, yidun, alibaba, prosopo, captchafox, vk, yandex, temu, binance, hunt, basilisk, cutcaptcha, atb
│
├── packages/                   # shared libs, импортятся services и solvers
│   ├── common/                 # типы, schemas, enums, config loader
│   ├── http/                   # ЯДРО HTTP-СТЕЛСА: curl_cffi wrapper, JA3/JA4 profiles, HTTP/2 settings, retry, proxy binding
│   ├── js_runtime/             # py-mini-racer sandbox, Node-sidecar client, script cache, обфускация-helpers
│   ├── sensors/                # per-engine sensor generators: akamai_sensor, px_sensor, datadome_sensor, kasada_payload и т.д.
│   ├── browser/                # camoufox/nodriver wrapper, profile mgmt, pool — ТОЛЬКО для fallback-воркера
│   ├── fingerprint/            # TLS JA3/JA4 профили + canvas/webgl/audio/font store
│   ├── proxy/                  # residential pool, ban-detection, rotation
│   ├── behavior/               # bezier mouse paths, human timing (для sensor-payload с coords events и для browser-fallback)
│   ├── models/                 # model-registry client, versioning, canary rollout
│   ├── queue/                  # Redis streams клиент
│   ├── db/                     # Postgres models, Alembic
│   ├── observability/          # logging (structlog), tracing (OpenTelemetry), metrics
│   └── crypto/                 # шифрование payload-ов, Rust ядро через pyo3 при необходимости
│
├── ml/                         # всё, что не онлайн-инференс
│   ├── training/               # train-скрипты по семействам
│   ├── eval/                   # валидация, бенчмарки, confusion-матрицы
│   ├── datasets/               # Dataset классы, augmentation, splits
│   ├── notebooks/              # эксперименты (в CI игнорятся)
│   └── configs/                # Hydra/OmegaConf yaml по экспериментам
│
├── data/                       # .gitignore — большой, лежит в S3/minio
│   ├── datasets/
│   ├── checkpoints/
│   ├── fingerprints/
│   ├── js_challenges/          # сэмплы obfuscated JS для reverse-команды, версии по датам
│   └── proxies/
│
├── infra/
│   ├── docker/                 # Dockerfile на каждый service
│   ├── compose/                # docker-compose.dev.yml, docker-compose.test.yml
│   ├── k8s/                    # Helm charts, манифесты по сервисам
│   ├── terraform/              # cloud (GPU-ноды, S3, proxy VPC)
│   └── monitoring/             # Prometheus rules, Grafana dashboards JSON
│
├── tools/
│   ├── labeler/                # human-in-loop UI (можно переиспользовать аннотатор из icon-matching проекта)
│   ├── runbook-check/          # CI: PR без обновлённого runbook на тип — не мержится
│   ├── dataset-sampler/        # сэмплинг живого трафика под разметку
│   ├── js-dumper/              # ловит obfuscated challenge JS с живых сайтов, кладёт в data/js_challenges/
│   ├── sensor-replayer/        # тестирует sensor-генератор против реальных эндпоинтов, для reverse-команды
│   └── scripts/                # ops / debug скрипты
│
├── runbooks/                   # per-captcha-type runbook, обязательный артефакт
│   ├── recaptcha_v2.md
│   ├── recaptcha_v3.md
│   ├── cloudflare_challenge.md
│   ├── turnstile.md
│   ├── datadome.md
│   ├── akamai.md
│   ├── kasada.md
│   ├── perimeterx.md
│   ├── funcaptcha_bda.md
│   └── ...                     # по одному на каждый тип из списка
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/             # живые кейсы, прогоняется перед релизом
│   └── canary/                 # сценарии для canary-бота (shared с services/canary)
│
├── docs/
│   ├── architecture.md
│   ├── protocol.md             # спека API gateway (2captcha-compat endpoints, payload vs URL mode)
│   ├── http_first.md           # принципы HTTP-first, когда включается fallback
│   ├── sensor_schemas.md       # документация sensor-форматов каждого антибот-энджина
│   ├── onboarding.md           # как новый инженер встаёт в строй за день
│   ├── adr/                    # architecture decision records
│   └── playbooks/              # incident response, on-call rotation
│
├── .github/workflows/          # CI: lint, test, build, deploy
├── pyproject.toml              # root workspace
├── uv.lock
├── docker-compose.yml          # dev-stack одной командой: gateway + redis + postgres + minio + worker-http + worker-cv
├── Makefile                    # make dev / make test / make release / make canary
└── README.md
```

---

## Ключевые принципы

1. **HTTP-first в solvers/.** Каждый солвер первым делом реализует `solve_http()`. Браузер — опциональный `solve_browser()` метод, который вызывается только роутером при явном URL-mode или в fallback-сценарии. 90% солверов вообще не имеют browser-пути.

2. **`packages/http/` + `packages/sensors/` — критичные.** Это то, на чём всё держится. Отдельная подкоманда RE работает в основном тут. Общие TLS-профили из `packages/http` переиспользуются везде.

3. **`packages/js_runtime/` — изолированная песочница.** v8 или Node-sidecar. Используется только для solvers, которым нужен obfuscated challenge (CF, FunCaptcha BDA, часть DataDome). Остальным не нужна.

4. **`solvers/` — плагины, не сервисы.** Каждый подкаталог регистрируется через entry_points (pyproject) в общий registry. Воркер при старте подгружает все доступные солверы. Новый тип добавляется одним подкаталогом, core не трогается.

5. **`runbooks/` в репе** — не в Confluence, не в Notion. Рядом с кодом, под git, CI проверяет что runbook обновлён вместе с кодом солвера или sensor-модуля. Защита от "знание в одной голове".

6. **`data/js_challenges/` в git-lfs или S3** — исторические сэмплы obfuscated JS с живых сайтов, версионированные по датам. Нужны для дебага и регресса, когда энджин мутирует.

7. **`ml/` отдельно от `solvers/`** — обучение и инференс в разных местах. В прод едет только inference-код + веса. `ml/training` может использовать GPU-кластер и не зависит от сервиса.

8. **`services/` — тонкие.** Всю логику в `solvers/` и `packages/`. Сервис это запускалка + health-checks + конфиг.

9. **Monorepo workspace** (uv workspace): корневой `pyproject.toml`, каждый package со своим `pyproject.toml`, общие deps пинуются в корне. Один `uv sync` настраивает всё.

---

## Dev-стак одной командой

```
make dev
```

Поднимает docker-compose:
- gateway (FastAPI, :8080)
- postgres (:5432)
- redis (:6379)
- minio (S3-compatible, :9000)
- worker-http (один инстанс, с mock-солверами)
- worker-cv (один инстанс, с mock-моделями)
- admin (:8081)

Browser-fallback и js-sidecar отдельным профилем: `make dev-full`.

Новый инженер клонирует репо, `uv sync && make dev` — всё работает за 5 минут. Это критерий onboarding-а.

---

## CI (GitHub Actions)

- `lint`: ruff + mypy на всём monorepo
- `test-unit`: pytest по packages/ и solvers/
- `test-integration`: docker-compose stack + pytest по services/
- `test-regression`: прогон живых кейсов на эталонных фикстурах
- `test-sensors`: отдельный stage для sensor-воспроизведения против фикстур из `data/js_challenges/`
- `build`: docker build + push в registry по тегу
- `runbook-check`: если менялся `solvers/<type>/` или `packages/sensors/<type>.py`, `runbooks/<type>.md` должен быть в том же PR
- `deploy-canary`: автодеплой на canary-env после merge в main
- `deploy-prod`: мануальный trigger после зелёной канарейки N часов

Self-hosted runner на GPU-ноде нужен для `test-integration` и ML-ретрейнов в CI.

---

## Что меняется в HTTP-first по сравнению с browser-first

| Было (browser-heavy) | Стало (HTTP-first) |
|---|---|
| Главный воркер — browser, 15-25 сессий/нода | Главный воркер — HTTP, 1000+ concurrent/нода |
| 6-12 browser-нод в проде | 1-3 browser-ноды только для fallback |
| Latency 500-5000ms | Latency 50-500ms |
| Reverse-команда: 2 человека | Reverse-команда: 3 человека + JS-runtime specialist |
| Нет packages/sensors, packages/js_runtime | Эти два пакета — критичные |
| Browser fingerprint в центре внимания | TLS JA3/JA4 + sensor-эмуляция в центре внимания |
| Proxy pool 500-2000 IP | Proxy pool 300-1500 IP (HTTP меньше сессий на IP) |
| OPEX проксей $5-15k/мес | OPEX проксей $3-10k/мес |
| GPU-нод 4-8 | GPU-нод 2-4 (только для ML-инференса) |
