# Runbook: deploy

> Порядок выкатки, проверки после неё и откат. Прод-топология в репозитории описана
> частично: compose — dev-стенд, TLS-ингресса и сервиса worker-browser в нём нет.
> Ниже — честная картина и минимальный прод-контур.

## 1. Что чем выкатывается

| компонент | в compose | как выкатывается |
|---|---|---|
| redis, postgres, minio | да | compose / managed |
| migrate | да, one-shot | `python -m captcha.services.cli init-db` до воркеров |
| gateway | да, `:8080` | образ, за TLS-ингрессом |
| worker-http | да | образ |
| worker-cv | да, `EXTRAS="--extra cv"` | образ с torch/opencv |
| worker-protocol | да | образ |
| **worker-browser** | **нет** | отдельный процесс/хост, нужен camoufox и ~16 ГБ RAM |
| **canary** | **нет** | отдельный процесс, нужен Redis и живой gateway |
| **TLS-ингресс** | **нет** | Caddy/nginx перед gateway, сертификаты, gateway не публикуется |

Один образ на все роли (`infra/docker/app.Dockerfile`), роль — `command:`, экстры —
build-arg `EXTRAS`.

Известные дыры образа для browser/hCaptcha:

- `camoufox` не входит ни в базовые зависимости, ни в extras → контейнерный
  worker-browser упадёт `ERR_CONFIG: camoufox is not installed`. Нужен отдельный образ/слой.
- `adapters/hcaptcha_js` требует Node + `npm ci` → HTTP-путь hCaptcha в текущем образе
  падает `ERR_JS_RUNTIME`/`ConfigError`, пока Node не установлен.
- `make full` монтирует `infra/monitoring/prometheus.yml`, которого нет в дереве —
  профиль `full` не поднимется, пока файл не добавлен.

## 2. Переменные окружения

Минимум для прода (полный список — `docs/operations.md` §2):

~~~
POSTGRES_URL=postgresql+asyncpg://…
REDIS_URL=redis://…
API_KEY_PEPPER=<случайная строка, не пустая>
ADMIN_TOKEN=<случайная строка>
CANARY_ALERT_WEBHOOK=https://…
S3_ENDPOINT=… S3_ACCESS_KEY=… S3_SECRET_KEY=… S3_BUCKET=…
LOG_JSON=true
~~~

Не коммитить. `.env.example` — только плейсхолдеры. Секреты — из секрет-стора/оркестратора.

## 3. Порядок выкатки

1. **Сборка.** `make build` (образ с `EXTRAS` под cv-ноды отдельно).
2. **Миграции.** `python -m captcha.services.cli init-db` (== `make migrate`). Одна ревизия
   вперёд за релиз; `0004_security` необратима по данным (см. §5).
3. **Инфраструктура.** Redis, Postgres, MinIO живы; `/readyz` пока 503 — это нормально,
   heartbeat-ов ещё нет.
4. **Воркеры.** Сначала `worker-http`, `worker-cv`, `worker-protocol`; они поднимут
   heartbeat, `/readyz` станет 200. Browser-воркер — на своей ноде:
   `python -m captcha.services.worker_browser --consumers 1`.
5. **Gateway.** После воркеров, чтобы он не отбивал типы без capabilities.
6. **Canary.** `python -m captcha.services.canary --once`; убедиться, что `total > 0`
   (пустой набор даёт ложно-зелёный результат, `runbooks/canary.md` §3).
7. **Smoke.** `make smoke KEY=<clientKey> TYPE=friendly_captcha`, затем один кейс из
   затронутого лейна.

Проверки после выкатки:

~~~bash
curl -fsS localhost:8080/healthz
curl -fsS localhost:8080/readyz   # {"ok":true,"redis":true,"postgres":true,"lanes":true,"live_lanes":[...]}
curl -fsS localhost:8080/metrics | grep -E 'captcha_(queue_depth|browser_sessions_active|type_health)'
~~~

## 4. Проверка конфигурации

- `ADMIN_TOKEN` задан → `/admin/*` отвечает 401 без заголовка и 200 с ним; пустой → 404.
- `API_KEY_PEPPER` задан → `make seed` печатает ключ, `make accounts` показывает только
  `key_tail`.
- `WORKER_BROWSER_SESSIONS × ~700 МБ < RAM ноды − 2 ГБ`.
- `WORKER_BROWSER_TASK_TIMEOUT_SEC` в 180–300 (иначе клампится).
- Логи JSON (`LOG_JSON=true`), без пейлоадов и ключей.

## 5. Откат

Порядок — обратный выкатке, с двумя ловушками:

1. **Приложение:** откатить образ gateway/воркеров на предыдущий тег.
2. **Миграции:** `alembic downgrade <rev>` только если схема несовместима со старым кодом.
   **Ниже `0004_security` откатываться нельзя без перевыдачи всех API-ключей** — плейнтекстов
   в базе нет, `downgrade` восстанавливает только схему.
3. **Модели:** переключить `latest` на предыдущую версию (`make models`, `make check-model`);
   артефакты иммутабельны.
4. **Типы:** если релиз включил тип раньше L3 — вернуть `experimental=True` и выкатить;
   гейт продажи должен перестать его отдавать (см. открытый вопрос в `docs/plan.md` §12).
5. **Canary:** `--once` после отката; pass rate не должен упасть относительно предыдущего
   прогона.

Чего откат **не** чинит: потерянные ключи (только хеши), потерянный Redis-state (задачи в
полёте, TTL 24 ч), отредактированные DLQ.

## 6. Деплой браузерной ноды

Отдельный хост, не воркер-контейнер из §1.

1. 16 ГБ RAM, 8+ vCPU, без GPU; `/tmp` на быстром диске (профили).
2. Установить пакет + `camoufox` и прогреть браузер (первый запуск скачивает сборку).
3. `WORKER_BROWSER_SESSIONS=12`, `WORKER_BROWSER_MIN_FREE_MB=2048`,
   `WORKER_BROWSER_RAMP_START=2`, `WORKER_BROWSER_TASK_TIMEOUT_SEC=240`.
4. `python -m captcha.services.worker_browser --consumers 1 --no-headless` — один раз
   глазами, затем headless.
5. Проверить `/readyz` (`live_lanes` содержит `browser`) и `captcha_browser_sessions_active`.
6. Держать `<temp>/captcha-browser-*` под наблюдением: reaper чистит при старте.
7. SIGTERM — штатный путь: drain, kill оставшихся сессий, снятие heartbeat.

## 7. Деплой canary

1. Отдельный аккаунт с лимитами и балансом.
2. `CANARY_FIXTURES_DIR` указывает на версионируемый каталог; `clientKey` — из секрета.
3. `CANARY_ALERT_WEBHOOK` задан, иначе алерты молчат.
4. Один экземпляр; `CANARY_INTERVAL_SEC=21600`.
5. После деплоя — `--once` и проверка `total > 0`.

## 8. Failure modes

| симптом после выкатки | причина | что делать |
|---|---|---|
| `/readyz` 503 `lanes:false` | воркеры не стартовали/не тот Redis | логи воркеров, `captcha:lane:*` |
| `/readyz` 503 `postgres:false` | миграции/пул/диск | `init-db`, пул, диск |
| gateway отбивает все типы `ERROR_TASK_NOT_SUPPORTED` | воркеры поднялись с пустым реестром (нет `--extra cv` для cv-типов) | сверить §2 и `/admin/solvers` |
| `ERROR_KEY_DOES_NOT_EXIST` на валидном ключе | разные `API_KEY_PEPPER` у gateway и сида | один пеппер на все процессы |
| браузерные задачи в очереди, сессий 0 | worker-browser не выкачен/упал | §6 |
| `ERR_JS_RUNTIME` на hCaptcha HTTP | нет Node/`npm ci` в образе | собрать образ с Node или оставить только browser |
| canary зелёный, но `total=0` | нет фикстур | `runbooks/canary.md` §3 |

## 9. Incident log

- `2026-11-15 — initial setup — compose поднимает dev-стенд; worker-browser, canary и TLS
  выкатываются вручную. Образ без camoufox/Node для hCaptcha-пути — известная дыра.`

## 10. References

- Код: `src/captcha/services/cli.py`, `infra/docker/app.Dockerfile`, `docker-compose.yml`
- Эксплуатация: `docs/operations.md` (конфиг, health, метрики, бэкапы, откат)
- Браузер: `runbooks/browser.md`; canary: `runbooks/canary.md`; hCaptcha: `runbooks/hcaptcha.md`
- Контракт: `docs/production-contract.md` §8 (TLS, admin token), §12 (merge rules)
