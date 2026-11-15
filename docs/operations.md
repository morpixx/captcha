# Эксплуатация

Как этот сервис живёт в проде: процессы, конфиг, health, метрики, бэкапы, откат, разбор
инцидентов. Пошаговые процедуры — в `runbooks/`:
[deploy](../runbooks/deploy.md), [browser](../runbooks/browser.md), [canary](../runbooks/canary.md),
[hcaptcha](../runbooks/hcaptcha.md). Индекс всех типов — [runbooks/README.md](../runbooks/README.md).

## 1. Топология

| процесс | команда | что делает | масштабирование |
|---|---|---|---|
| gateway | `python -m captcha.services.gateway` | REST, авторизация, квоты, биллинг, XADD | горизонтально, stateless |
| worker-http | `python -m captcha.services.worker_http --consumers 8 --concurrency 512` | I/O-лейн, эскалация в browser | горизонтально |
| worker-cv | `python -m captcha.services.worker_cv` | ML-инференс | по GPU-нодам |
| worker-protocol | `python -m captcha.services.worker_protocol` | PoW/сенсоры, CPU | горизонтально |
| worker-browser | `python -m captcha.services.worker_browser --consumers 1` | Camoufox, 12 сессий | по 1–3 нодам, RAM-bound |
| canary | `python -m captcha.services.canary` | прогон фикстур каждые 6 ч | один экземпляр |
| pipeline | `python -m captcha.services.pipeline` | сэмплинг/голден/ретрейн | по расписанию |
| caddy | `docker compose` (сервис `caddy`) | TLS-ингресс 80/443 → gateway | одна реплика на домен |

`docker-compose.yml` описывает redis, postgres, minio, migrate, gateway, worker-http,
worker-protocol, worker-cv, а также worker-browser (профиль `browser`) и canary (профиль
`canary`). Базовый `make dev` их не поднимает: браузерной ноде нужен camoufox и ~16 ГБ RAM,
canary — фикстуры. Вручную: `docker compose --profile browser --profile canary up -d` или
`runbooks/deploy.md`.

Наружу публикуется только `caddy` (80/443, ACME при заданном `CAPTCHA_DOMAIN`, иначе
внутренний CA); gateway — `expose: 8080` внутри сети compose. Дев-оверлей
(`docker-compose.dev.yml`) выключает Caddy профилем `edge` и публикует
`127.0.0.1:8080`.

Лейны в Redis: `captcha:stream:{http,cv,protocol,browser}`, DLQ — `captcha:dlq`,
состояние задачи — `captcha:task:<id>` (TTL 24 ч), квоты — `captcha:quota:*`,
heartbeat — `captcha:lane:<lane>:<worker>` (TTL 90 с), capabilities — `captcha:cap:<lane>`,
type health — `captcha:typehealth:<type>` (TTL 12 ч).

## 2. Конфигурация

Все настройки — `src/captcha/config.py`, читаются один раз в composition root. Значения по
умолчанию рабочие для dev; прод обязан переопределить секреты и лимиты.

| группа | переменные |
|---|---|
| gateway | `CAPTCHA_GATEWAY_HOST`, `CAPTCHA_GATEWAY_PORT`, `CAPTCHA_GATEWAY_WORKERS` |
| redis | `REDIS_URL`, `REDIS_STREAM_PREFIX` |
| postgres | `POSTGRES_URL`, `POSTGRES_POOL_SIZE` |
| s3 | `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION` |
| workers | `WORKER_HTTP_CONCURRENCY`, `WORKER_HTTP_IMPERSONATE`, `WORKER_CV_DEVICE` |
| browser | `WORKER_BROWSER_SESSIONS`, `WORKER_BROWSER_HEADLESS`, `WORKER_BROWSER_MIN_FREE_MB`, `WORKER_BROWSER_TASK_TIMEOUT_SEC`, `WORKER_BROWSER_RAMP_START`, `WORKER_BROWSER_CONSUMERS` |
| canary | `CANARY_INTERVAL_SEC`, `CANARY_ALERT_WEBHOOK`, `CANARY_FIXTURES_DIR`, `CANARY_PASS_THRESHOLD`, `CANARY_POLL_INTERVAL_SEC`, `CANARY_TASK_TIMEOUT_SEC`, `CAPTCHA_GATEWAY_URL` |
| router | `HTTP_FALLBACK_THRESHOLD`, `HTTP_FALLBACK_WINDOW_SEC` |
| proxy | `PROXY_PROVIDER_PRIMARY`, `PROXY_PROVIDER_FALLBACK`, `PROXY_POOL_FILE` |
| llm | `LLM_ENDPOINT`, `LLM_MODEL`, `LLM_API_KEY` |
| observability | `LOG_LEVEL`, `LOG_JSON`, `OTEL_ENDPOINT`, `SENTRY_DSN` |
| security | `ADMIN_TOKEN`, `API_KEY_PEPPER`, `RATE_LIMIT_CREATE_PER_MIN`, `MAX_ACTIVE_TASKS_PER_ACCOUNT`, `MAX_DAILY_TASKS`, `MAX_DAILY_SPEND`, `ALLOW_EXPERIMENTAL_TASKS` |
| pingback | `CALLBACK_TIMEOUT_SEC`, `CALLBACK_MAX_ATTEMPTS` |

**Расхождение:** `.env.example` перечисляет browser/canary/security-переменные, но не
содержит `ALLOW_EXPERIMENTAL_TASKS` — в beta он по умолчанию `1`, а прод по контракту §7
обязан ставить `0` (иначе продаются типы, у которых единственный солвер `experimental`).
До правки примера сверяйтесь с `config.py`.

Обязательные для прода: `API_KEY_PEPPER` (непустой), `ADMIN_TOKEN`, `POSTGRES_URL`,
`REDIS_URL`, `CANARY_ALERT_WEBHOOK`. Пустой `ADMIN_TOKEN` = выключенная админка, пустой
пеппер = хеши без защиты от rainbow-таблиц.

## 3. Health и readiness

- `GET /healthz` — процесс жив, зависимостей не трогает.
- `GET /readyz` — 200 только если: Redis отвечает, Postgres отвечает, есть хотя бы один
  свежий heartbeat лейна (`captcha:lane:*` моложе 90 с). Иначе 503 с разбивкой
  `{redis, postgres, lanes}` и причинами. Проброс на прокси/балансировщик — по `/readyz`,
  liveness — по `/healthz`.
- `GET /metrics` — Prometheus.

Типичные чтения `/readyz`:

| ответ | что значит | что делать |
|---|---|---|
| `lanes: false` | воркеры не битят (или Redis не тот) | проверить процессы воркеров и `captcha:lane:*` |
| `postgres: false` | БД недоступна | смотреть пул/диск; gateway отвечает 5xx на всё |
| `redis: false` | очереди и состояние недоступны | сервис фактически мёртв, поднимать Redis |

## 4. Метрики и алерты

Канонические серии (контракт §10):

| метрика | тип | что показывает |
|---|---|---|
| `captcha_tasks_total{type,lane,status}` | counter | приёмка/терминальные статусы |
| `captcha_task_latency_seconds{type,solve_mode}` | histogram | end-to-end latency |
| `captcha_queue_depth{lane}` | gauge | затор в лейне |
| `captcha_browser_sessions_active` | gauge | живые сессии браузера |
| `captcha_browser_launch_seconds` | histogram | время запуска Camoufox |
| `captcha_browser_rss_bytes` | gauge | RSS всех деревьев браузера |
| `captcha_escalations_total{type}` | counter | HTTP → browser прыжки |
| `captcha_canary_pass_rate{type}` | gauge | последний прогон канарейки |
| `captcha_type_health{type}` | gauge | 1 = ok, 0 = degraded |
| `captcha_dlq_total{lane,reason}` | counter | мёртвые конверты |

Дополнительно: `captcha_tasks_accepted_total`, `captcha_tasks_done_total`,
`captcha_solver_errors_total{type,error_code}`, `captcha_active_tasks{lane}`,
`captcha_proxy_bans_total{provider}`, `captcha_sensor_build_latency_seconds{engine}`.

Предлагаемые алерты (пороги — стартовые, тюнить по трафику):

| условие | серьёзность | реакция |
|---|---|---|
| `captcha_type_health{type} == 0` 2 прогона подряд | warning | `runbooks/canary.md` §5 |
| `captcha_queue_depth{lane} > 1000` 10 мин | critical | воркер лейна жив? `/readyz` |
| `rate(captcha_dlq_total[15m]) > 1` | warning | разобрать reason, не тело (DLQ редактирован) |
| `captcha_browser_rss_bytes` > 80% RAM ноды | warning | снизить `WORKER_BROWSER_SESSIONS` |
| `captcha_browser_sessions_active` == 0 при трафике browser-типов | critical | воркер/провайдер упал |
| `captcha_escalations_total` растёт по типу | info | HTTP-путь деградировал, смотреть canary |
| `/readyz` != 200 5 мин | critical | см. §3 |

## 5. Бэкапы

| что | как | частота | проверка |
|---|---|---|---|
| Postgres (аккаунты, задачи, леджер, payment_intents) | `pg_dump -Fc` + WAL-архив или managed-бэкап | ежедневно + PITR | ежемесячный restore в отдельную БД |
| Redis | RDB-снапшот (`save 60 1` в compose) + AOF в проде | ежечасно | restore не критичен: state живёт 24 ч |
| S3 (веса, golden, датасеты) | версионирование бакета + lifecycle | непрерывно | `make models`, `make check-model` |
| Секреты | секрет-стор, не бэкап репозитория | при ротации | ротация пеппера = перевыдача ключей |

Что **не** бэкапится осознанно:

- `captcha:task:*` и streams: TTL 24 ч, после потери клиент получает
  `ERROR_TASK_NOT_FOUND` и создаёт задачу заново;
- браузерные профили: временные, одноразовые;
- DLQ: редактирован, для разбора хватает кода ошибки, не тела.

Восстановление после потери Redis: задачи в полёте потеряны (клиенты повторят), gateway
поднимается, воркеры пересоздают consumer-группы. После потери Postgres: восстанавливать
только из бэкапа — иначе пропадут аккаунты и леджер, и ключи клиентов не восстановить
(хранится только хеш).

## 6. Откат

Порядок отката — слой за слоем, не всё сразу:

1. **Приложение.** Откатить образ/коммит gateway и воркеров. Схема БД должна быть совместима
   со старой версией; иначе сначала откат миграций.
2. **Миграции.** `alembic downgrade <rev>` работает для схемы, но `0004` (хеши ключей)
   **необратима по данным**: плейнтекстов больше нет. Откат ниже `0004` = перевыдача всех
   ключей. Планировать откат так, чтобы не проходить эту границу.
3. **Модели.** `make publish-model` публикует версию, `latest` указывает на текущую;
   откат — переключить `latest` на предыдущую версию в реестре (`make models`,
   `make check-model`). Артефакты иммутабельны: новая версия — новое имя.
4. **Конфиг.** Откат env — рестарт процессов; `get_settings()` кэшируется на процесс.
5. **Типы.** Понижение типа до `experimental=True` — правка `SolverInfo` + деплой; гейт
   продажи по L3 должен отреагировать (см. `docs/plan.md` §2 и открытый вопрос №7).

Проверка после отката: `/readyz` 200, `make check`, один `make smoke` по затронутому типу,
канарейка `--once` (фикстуры есть на `friendly_captcha` и `altcha`; для остальных типов
прогон пустой и ничего не доказывает).

## 7. Ёмкость

- **HTTP-нода:** 1000+ concurrent на 16–32 vCPU, `WORKER_HTTP_CONCURRENCY` = потолок
  in-flight.
- **CV-нода:** 1 воркер на GPU; `WORKER_CV_CONCURRENCY` больше 4 обычно вредит VRAM.
- **Browser-нода:** 16 ГБ → 12 сессий (~700 МБ на страницу + запас), guard 2 ГБ свободных.
  Меньше 8 ГБ — 6 сессий и `WORKER_BROWSER_MIN_FREE_MB=1024`. Больше 20 не имеет смысла:
  CPU и профили становятся узким местом.
- **Canary:** один экземпляр, последовательный прогон; параллельный запуск искажает p95.

## 8. Разбор инцидентов

Быстрый порядок для «тип перестал решаться»:

1. `/readyz` и `captcha:typehealth:<type>` — жив ли лейн и что говорит канарейка;
2. `captcha_solver_errors_total{type}` — какой код доминирует (`ERR_SENSOR` ≠ `ERR_PROXY`);
3. `runbooks/<type>.md` §6 — таблица failure modes;
4. `captcha_escalations_total{type}`: растёт — HTTP-путь деградировал, браузер вытягивает;
5. DLQ: `captcha_dlq_total{lane,reason}`, тело не читается (редактировано) — только код;
6. если тип браузерный — `runbooks/browser.md` §6 (сессии, RAM, запуск, профили).

Отдельные сценарии:

- **Очередь растёт, воркер жив** — потолок concurrency или медленный внешний вызов; смотреть
  `captcha_task_latency_seconds` p95 и `captcha_active_tasks`.
- **`lanes: false`** — heartbeat протух: воркеры не стартовали, Redis другой, или упал
  единственный инстанс лейна.
- **Пустой canary** — `reason=no_fixtures`, pass rate 1.0: это не здоровье, это отсутствие
  измерения (`runbooks/canary.md` §3).
- **Утечка профилей** — `<temp>/captcha-browser-*` копится: reaper не сработал, смотреть
  `runbooks/browser.md` §6.

## 9. Регулярные работы

| когда | что |
|---|---|
| ежедневно | `/readyz`, DLQ, type health, свободное место (Postgres, профили) |
| еженедельно | разбор канарейки, тренд `escalations_total`, бан-рейт прокси |
| ежемесячно | restore-тест Postgres, ротация ключей по необходимости, апдейт camoufox/curl_cffi |
| по релизу | `make check`, миграции, smoke, canary `--once`, обновление runbook-а типа |