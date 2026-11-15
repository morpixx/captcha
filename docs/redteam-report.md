# Red-team отчёт (W1, feat/redteam)

Скоуп: gateway, адаптеры, воркеры, очередь, конфигурация и infra-файлы дерева.
Метод: чтение кода + офлайн-проверки (юнит/ASGI-тесты, grep, статический разбор);
живой стенд, браузер, реальная сеть, `docker compose up` не запускались — это
условие задачи и ограничение отчёта.

Гейт на ветке `feat/redteam`:

```
D:/captcha/.venv/Scripts/python.exe -m pytest -q      -> 1982 passed, 2 skipped
D:/captcha/.venv/Scripts/python.exe -m ruff check src tests -> All checks passed
```

До правок: 1887 passed, 2 skipped. Добавлено 95 тестов в пяти новых файлах.

Severity:

| уровень | значение |
|---|---|
| Critical | эксплуатируется клиентским аккаунтом, ломает сетевой периметр или деньги без дополнительных условий |
| High | утечка секретов/данных либо SSRF с ограничением; нужно условие (конфиг, тип, редирект) |
| Medium | ослабление защиты, нужно дополнительное условие или доступ к хосту |
| Low | гигиена, доки, защита в глубину |

## 0. Резюме

| ID | severity | область | статус |
|---|---|---|---|
| RT-01 | Critical | SSRF: клиентские URL (`pageurl`, `challenge_url`, `surl`, `api_server`, …) не проверялись в HTTP-лейне | **fixed** |
| RT-02 | Critical | SSRF: `callbackUrl` принимался любым, воркер POST-ил на него | **fixed** |
| RT-03 | High | пароль Redis/Postgres в логах воркеров (DSN целиком) | **fixed** |
| RT-04 | High | `text_captcha`: per-task `endpoint` при пустом `LLM_ENDPOINT` → POST на чужой хост + утечка `LLM_API_KEY` | **fixed** |
| RT-05 | High | browser lane: редиректы и подресурсы идут на приватные адреса до проверки | open |
| RT-06 | High | HTTP-транспорт: `allow_redirects=True`, редиректы не проверяются, полный URL в контексте ошибки | open |
| RT-07 | Medium | Docker: egress не ограничен, metadata/внутренние сервисы достижимы | open |
| RT-08 | Medium | Redis RDB на диске содержит конверты с прокси-креденшелами и cookies | open |
| RT-09 | Medium | общий volume `browser-tmp` + `reap_stale` → реплика сносит живой профиль другой | open |
| RT-10 | Low | токен в `callbackUrl` попадал в логи (path/query) | **fixed** |
| RT-11 | Low | гонка резервирования квоты (check → record) | accepted by design |
| RT-12 | Low | у gateway нет своего лимита тела; 40 МБ держит только Caddy | open |
| RT-13 | Low | clientKey в query string у GET-вариантов и legacy | open (info) |
| RT-14 | Low | `hmac.compare_digest` на `str` с не-ASCII `ADMIN_TOKEN` → 500 | open |
| RT-15 | Low | мёртвый неохраняемый путь `browser/pool.py` + `Session.goto_with_stealth` | open |
| RT-16 | Low | (unverified) текст ошибки запуска camoufox может содержать прокси-креды | open |
| RT-D1 | info | `SECURITY.md`/`operations.md` расходятся с кодом | open (docs owner) |

## 1. Findings

### RT-01 — Critical — SSRF через клиентские URL в HTTP-лейне (fixed)

**Что было.** `adapters/netguard.py` существовал и был корректен, но подключён
ровно в одном месте: `adapters/browser/provider.py:198` (навигация). Ни
`routes/tasks.py`, ни `routes/legacy.py` не проверяли URL из payload до
постановки задачи и списания. Контракт §8 требует проверять `pageurl`,
`websiteURL`, `challenge_url`, `surl`, `api_server` — вызовов не было.

Это не теория: HTTP-солверы строят из этих полей реальные запросы.

- `solvers/funcaptcha/settings.py:48-55` — `SURL_KEYS` включает `surl`,
  `api_server` и `host`; `normalize_surl` (`:97-108`) пропускает любой
  `http(s)`-хост; `fetch_settings` (`:122-126`) делает
  `GET {surl}/v2/{public_key}/api.js`.
- `solvers/antibot/datadome/payload.py:38-59`, `castle/payload.py:21-30`,
  `tspd/solver.py:126-130`, `akamai/wire.py:98-102` — тот же `page_url` из
  payload.
- `solvers/interactive/geetest/endpoints.py:49-70` — `api_server` становится
  базой для `gettype.php`/`ajax.php`.

**PoC (до фикса, по коду).** Аутентифицированный клиент:

```json
POST /createTask
{"clientKey":"...","task":{"type":"FunCaptchaTaskProxyless",
 "websiteURL":"https://example.com","surl":"http://169.254.169.254"}}
```

Задача проходила гейт типов, квоты и оплату; воркер шёл на
`http://169.254.169.254/v2/<key>/api.js` со своего egress. Слепой SSRF в
loopback/private/metadata и в любой HTTP-сервис compose-сети (Redis, Postgres,
внутренние админки). Ответ клиенту не отдавался, но запрос и его побочные
эффекты (POST на внутренние ручки) происходили.

**Fix.**

- `adapters/netguard.py:80-91` — новый `ensure_public_url_shape`: все проверки
  `ensure_public_url` (схема, длина 2048, control-символы, порт,
  `localhost`/`*.localhost`/`metadata.google.internal`, приватные
  IP-литералы, IPv4-mapped IPv6) без DNS; общий `_shape_check` с полной
  версией (`:124-173`), поведение `ensure_public_url` не изменено.
- `services/gateway/url_guard.py` — `validate_client_urls`: 27 ключей payload
  (включая найденные при аудите `host`, `domain`, `script_url`, `sbsd_url`,
  `tl_url`, `mfc_url`, `widget_url`, `ips_url`, …) + `callbackUrl`;
  образные поля (`image_url`, `captcha_img`, `captchaImg`) проверяются только
  если уже начинаются с `http(s)://` — base64-картинка VK URL-ом не считается.
  Голые хосты (`client-api.arkoselabs.com`) валидируются как `https://…`.
- `routes/tasks.py:89` и `routes/legacy.py:155` — вызов **до** `charge`/`check`;
  отказ: `ERROR_BAD_REQUEST` (errorId 21) в JSON API и
  `ERROR_BAD_PARAMETERS` в `/in.php`, без списания и без XADD. В лог идут
  только `field`/`host`/`address`, не URL (`pageurl` может нести токен).

**Тесты.** `tests/gateway/test_url_guard.py` (50): каждый URL-ключ × приватные
литералы; `169.254.169.254`, `[::1]`, `fd00::1`, `localhost`,
`metadata.google.internal`, `file://`, `javascript:`; оба API-пути;
assert «не списали, не поставили в очередь, не создали строку»; публичные URL и
голые хосты проходят; base64 в `captcha_img` не трогается; `endpoint` (LLM)
не трогается; `tests/adapters/test_netguard_shape.py` (19) — shape-проверка не
вызывает DNS (`socket.getaddrinfo` замокан на падение).

**Остаточный риск.** Хост, чей DNS-ответ — приватный адрес, и rebinding между
create и fetch этой проверкой не закрываются: она намеренно без DNS, чтобы не
блокировать event loop на самом горячем пути и не делать тесты сетевыми.
Закрытие — RT-06 (проверка на каждом хопе в момент соединения).

### RT-02 — Critical — SSRF через `callbackUrl` (fixed)

**Что было.** `services/gateway/schemas.py:47` принимал любую строку,
`routes/tasks.py` писал её в `tasks.callback_url`, воркер
(`worker_http/worker.py`, `_send_pingback`) звал `PingbackSender.send` →
`httpx post(url, json=payload)`. Никакой проверки ни на входе, ни на отправке:
POST с JSON на `http://127.0.0.1:…`, `http://169.254.169.254/`, на любую
внутреннюю ручку. Слепой, но с телом и с повтором (3 попытки).

**Fix.** Тот же guard на create (`tasks.py:89`) и повторная проверка
непосредственно перед отправкой (`adapters/pingback.py:69-78`) — на случай
строк, созданных до фикса. Отказ логируется как `pingback.url_rejected`
(field/host), URL не печатается. Редиректы pingback не преследует
(`follow_redirects` у httpx по умолчанию `False`), так что «публичный
callback → 302 на 10.0.0.1» не срабатывает.

**Тесты.** `tests/adapters/test_pingback_ssrf.py` (11): приватные литералы,
`localhost`, metadata, `file:`, `javascript:` — `send() == False` и клиент не
вызван; публичный URL доставляется один раз; не-2xx ретраится; `safe_url`
вырезает path/query/userinfo.

### RT-03 — High — Пароли Redis/Postgres в логах (fixed)

**Что было.** `services/worker_http/cli.py` писал `settings.redis_url`
целиком (`redis.connected`, `redis.ping_failed`), а
`worker_browser/worker.py`, `worker_cv/worker.py`,
`worker_protocol/worker.py` клали `self.settings.redis_url` в `context`
`ConfigError`. При `REDIS_URL=redis://:password@host:6379/0` пароль уходил в
stdout-логи (и в агрегатор). Gateway логировал через `split("@")`, что
безопасно только при наличии `@`.

**Fix.** `domain/redaction.py:73-99` — `redact_dsn`: userinfo → `***`,
query выброшен, path сохранён, IPv6 в скобках; не-DSN → `***`. Применён в
`worker_http/cli.py:148,151`, трёх воркерах (`worker_browser/worker.py:225`,
`worker_cv/worker.py:116`, `worker_protocol/worker.py:109`) и
`gateway/app.py:126`.

**Тесты.** `tests/domain/test_redaction_dsn.py` (10): с паролем/без, query,
IPv6, мусор, `None`; отдельно «пароль не возвращается никогда».

### RT-04 — High — `text_captcha`: endpoint override при пустом `LLM_ENDPOINT` (fixed)

**Что было.** `solvers/ocr/_llm.py:76-84`:

```python
if configured and _origin(override) != _origin(configured):
    raise ConfigError(...)
return normalize_endpoint(override)
```

Если оператор выключил локальную модель, выставив `LLM_ENDPOINT=""`, условие
ложно и клиентский `endpoint` принимался как есть. Солвер делал POST на
указанный хост, а `text_captcha.py:75-79` при непустом `LLM_API_KEY`
прикладывал `Authorization: Bearer <ключ>`. То есть SSRF + эксфильтрация
ключa модели одним полем payload.

**Fix.** `_llm.py:79-86` — при пустом конфиге override отклоняется
(`ConfigError`, без запроса). При заданном конфиге правило прежнее:
совпадение origin обязательно.

**Тесты.** `tests/solvers/ocr/test_llm_endpoint_policy.py` (5): отказ при
пустом конфиге, отказ metadata-хоста, разрешённый same-origin override,
отказ другого origin, работа без override.

### RT-05 — High — Browser lane: редирект/подресурсы на приватный адрес (open)

**Evidence.** `adapters/browser/provider.py:191-214`: `ensure_public_url` для
стартового URL, затем `page.goto`, затем проверка `final_url`. Проверка
финального URL идёт **после** навигации: браузер уже сходил на
`http://10.0.0.1/`, если сайт ответил таким `Location`. Подресурсы
(`<img>`, `fetch`, `iframe`, `<script>`) не проверяются вообще — это запросы
внутри страницы, до `goto`-проверки они не доходят.

**Влияние.** Страница-цель (или клиент, подсунувший такой `pageurl`) может
заставить браузер воркера сканировать внутреннюю сеть и metadata. Данные
клиенту не возвращаются, но факт запроса и тайминги утекают через поведение
задачи; на metadata с IMDSv1 это ещё и кража креденшелов ноды, если их
получится вынести через страницу.

**Почему не исправлено.** Требует перехвата запросов внутри Camoufox
(`page.route("**/*")` с проверкой хоста каждого запроса) и живого браузера
для тестов — а live/браузер/сеть по условию задачи не трогаются; файлы
browser-core принадлежат другому владельцу. Рекомендация: route-хук,
проверяющий каждый request URL через `ensure_public_url`, плюс egress-политика
RT-07 как второй слой.

### RT-06 — High — HTTP-транспорт: редиректы и URL в ошибках (open)

**Evidence.** `adapters/transport/curl.py:162` — `allow_redirects: bool = True`
по умолчанию, `:177` — флаг уходит в curl_cffi. `ensure_redirect` нигде не
вызывается (grep по `src`: только определение в `netguard.py:94`). Контекст
`ProxyError` (`:190-193`), `ChallengeExpiredError` (`:206-217`) содержит
полный `url`/`final_url`; солверы логируют URL сами (например
`solvers/antibot/datadome/solver.py:90`). Значит: (а) публичный `surl`,
редиректящий на `http://10.0.0.1/`, преследуется; (б) query-токен из
`pageurl` оседает в логах и в `tasks.error` Postgres.

**Рекомендация.** Для клиентских URL: `allow_redirects=False` + ручной разбор
`Location` с `ensure_redirect` на каждом хопе; либо ограничить fetch
origin-ом `site_url`. В контекстах ошибок оставить схему+хост (как
`redact_dsn`/`safe_url`). Это правка адаптера транспорта — не делал в
red-team проходе, чтобы не менять поведение 60+ солверов без live-прогона.

### RT-07 — Medium — Docker/egress: metadata и внутренние сервисы достижимы (open)

**Evidence.** `docker-compose.yml`: ни одной сети с `internal: true`, ни
egress-политики; `redis`/`postgres`/`minio` опубликованы на `127.0.0.1`
(`:49`, `:66`, `:82-84`) с дефолтными паролями (`captcha:captcha`,
`minioadmin`), но изнутри compose-сети доступны всем сервисам по имени.
Воркеры имеют неограниченный egress: `169.254.169.254`, `100.100.100.200`,
хост-сеть, любые внутренние HTTP-ручки.

**Влияние.** Пока SSRF не закрыт полностью (RT-05/06), это усилитель: даже
ограниченный SSRF достаёт до metadata. Дефолтные пароли — риск при любом
доступе в сеть (скомпрометированный воркер/контейнер).

**Рекомендация.** Отдельная сеть `internal: true` для datastore, egress через
proxy/файрвол на нодах воркеров, IMDSv2 с `HttpPutResponseHopLimit=1`
(и `HttpTokens=required`), обязательные непустые пароли из секрет-стора
(compose уже читает `POSTGRES_URL` из `.env` — но дефолт остаётся в файле).

### RT-08 — Medium — Redis persistence хранит креденшелы (open)

**Evidence.** `docker-compose.yml:50` — `redis-server --save 60 1`,
`:51` — volume `redis-data`. В Redis лежит полный конверт
(`adapters/queue/state_store.py:97-108` — `Task` целиком, включая
`task.proxy`) с TTL 24 ч, плюс streams с тем же конвертом. RDB-снапшот
кладёт прокси-логины/пароли и cookies задач на диск.

**Влияние.** Компрометация хоста/снапшота/бэкапа тома = пул прокси и cookies
клиентов. `docs/operations.md` §5 считает Redis-потерю некритичной, но не
говорит о содержимом на диске.

**Рекомендация.** Для task-state Redis — `--save ""` (или отдельный
инстанс с эфемерным томом), шифрование тома на уровне диска, либо вынос
прокси-креденшелов из конверта (ссылка на запись в pool вместо строки).

### RT-09 — Medium — Общий `browser-tmp` и `reap_stale` между репликами (open)

**Evidence.** `docker-compose.yml:206` монтирует named volume `browser-tmp` в
`/tmp` воркера; `adapters/browser/supervisor.py:145-167` (`reap_stale`)
удаляет **все** каталоги `captcha-browser-*`, которых нет в его собственном
реестре. Две реплики `worker-browser` на одном хосте (общий volume) видят
каталоги друг друга: реплика A при старте/первой сессии сносит живой профиль
реплики B вместе с процессом (в пределах своего PID-namespace kill не
достанет, но каталог профиля — общий).

**Влияние.** Падения задач и «загадочные» ошибки браузера при масштабировании
на одном хосте; `docs/production-contract.md` §4 разрешает 1–3 ноды, compose
позволяет `--scale`, так что сценарий реальный.

**Рекомендация.** Префикс профиля с instance id (`captcha-browser-<instance>-`)
и reap только своего префикса, либо `tmpfs`/отдельный volume на реплику.

### RT-10 — Low — Токен callback URL в логах (fixed)

`worker_http/worker.py` логировал `callback_url` целиком (в
`task.pingback`/`task.pingback_crashed`), `adapters/pingback.py` — `url` в
каждом варнинге. Callback часто содержит `?token=`.

**Fix.** `adapters/pingback.py:34-48` — `safe_url` (схема+хост+порт, без
userinfo/path/query); используется в sender-логах и в
`worker_http/worker.py:419,422`. Тест в `tests/adapters/test_pingback_ssrf.py`.

### RT-11 — Low — Гонка квоты (accepted by design)

`services/gateway/quotas.py:13-18` прямо описывает: резерв не транзакционный,
два запроса на границе окна могут превысить лимит на единицу; active-ZSET
чистится по возрасту (300 с), а не по факту завершения — ошибка в
консервативную сторону. Проверка идёт **до** списания в обоих путях
(`routes/tasks.py:106-107`, `routes/legacy.py:173-174`), обхода через `/in.php` нет
(тест `tests/gateway/test_legacy_security.py::test_legacy_in_quota_rejection_is_not_billed`).
Оставляю как есть; если понадобится строгость — Lua-скрипт на check+record.

### RT-12 — Low — Нет лимита тела на gateway (open)

`grep add_middleware` по `services/gateway` пуст; 40 МБ ограничивает только
Caddy (`infra/caddy/Caddyfile:35-37`). Прод-схема публикует только Caddy, так
что дыра закрыта по топологии, но dev-overlay (`docker-compose.dev.yml`)
публикует gateway на `127.0.0.1:8080`, а изнутри compose-сети gateway открыт
всем: неограниченный body → парсинг до аутентификации → память/CPU DoS.
Рекомендация: ASGI-middleware, читающая `Content-Length` и обрезающая поток
(дефолт 40 МБ = текущий Caddy-лимит).

### RT-13 — Low — clientKey в query string (open, info)

GET-варианты `/getTaskResult`, `/getBalance`, `/in.php`, `/res.php` берут
ключ из query (2captcha-совместимость). В логах его нет:
`gateway/cli.py:52` — `access_log=False`, у Caddy access log выключен
осознанно (`Caddyfile:10-14`). Но ключ оседает в истории/Referer/прокси
клиента. Документировать «POST для новых интеграций», GET оставить только для
совместимости.

### RT-14 — Low — admin token: `compare_digest` на не-ASCII → 500 (open)

`routes/admin.py:32`: `hmac.compare_digest(x_admin_token, expected)` на двух
`str` бросает `TypeError`, если в значении есть не-ASCII. Практически:
`ADMIN_TOKEN` с юникодом даёт 500 вместо 401 на любой запрос к `/admin/*`.
Фикс на одну строку (`…encode()` оба аргумента) — но это не про эксплойт;
оставил в бэклоге.

### RT-15 — Low — Мёртвый неохраняемый browser-путь (open)

`adapters/browser/pool.py` и `adapters/browser/session.py:107`
(`goto_with_stealth`) не имеют netguard-проверки. Импортёров нет
(`grep -rn "browser.pool|goto_with_stealth"` по `src` — только определения),
то есть код мёртв, но при «оживлении» станет SSRF-дырой мимо provider.goto.
Рекомендация: удалить `pool.py`/`session.py` либо делегировать проверку в
provider.

### RT-16 — Low — (unverified) прокси-креды в тексте ошибки запуска (open)

`adapters/browser/provider.py:135-139` оборачивает любое исключение
Camoufox в `CaptchaError(context={"job_id":…, "error": str(exc)})`; этот
контекст уходит в `set_failed` → Redis (24 ч) и в `tasks.error` Postgres.
Сообщения playwright/camoufox иногда цитируют launch options целиком, где
лежит `proxy: {server, username, password}`. Проверить без живого браузера
нельзя, поэтому помечаю unverified. Рекомендация: вычищать
`password`/`username` из `str(exc)` перед упаковкой (общий helper рядом с
`redact_dsn`).

### RT-D1 — info — доки разошлись с кодом (docs owner)

- `SECURITY.md` §3: «netguard реализован, но ещё не подключён» — теперь
  подключён на gateway (этот отчёт, RT-01/02), на browser-навигации был и
  раньше.
- `SECURITY.md` §6 и `docs/architecture.md` «Что ещё не сделано» п.3: «TLS
  ingress в репозитории нет» — есть: `infra/caddy/Caddyfile` + сервис
  `caddy` в `docker-compose.yml:247-263`, gateway не публикует порт.
- `docs/operations.md` §2 «`.env.example` не перечисляет browser/canary/
  security-переменные» — перечисляет (строки 42-88 `.env.example`).

Правил бы в одной ветке с docs-владельцем, чтобы не плодить конфликты.

## 2. Что проверено и претензий нет

- **API-ключи.** `accounts.api_key` = NULL, хранится `api_key_hash`
  (HMAC-SHA256 с `API_KEY_PEPPER`) + `key_tail`; единственный поиск —
  `AccountRepo.get_by_key` по хешу (`adapters/storage/repos.py:36-41`);
  миграция `0004_security` бэкфилит и обнуляет плейнтекст; downgrade
  восстанавливает только схему. Длина ключа 8–64 проверяется до БД.
- **Отключённые аккаунты.** `gateway/auth.py:46-50` — 403
  `ERROR_ACCOUNT_SUSPENDED`; `AccountRepo.charge` дополнительно требует
  `disabled = false` в UPDATE, то есть отключённый аккаунт не тратит баланс
  даже при гонке с отключением.
- **Админка.** Пустой `ADMIN_TOKEN` → 404, неверный → 401, сравнение
  constant-time, ручки только читают (`routes/admin.py:24-33`).
- **Redaction.** Postgres-payload без `proxy*`/`cookies` на любой глубине,
  DLQ и retry-копии редактированы, нечитаемые байты в DLQ не копируются
  (`adapters/queue/streams.py:52-64`, `domain/redaction.py`). Покрыто
  `tests/gateway/test_security_hardening.py`.
- **Квоты.** Оба пути создания, проверка до списания, отказ
  `ERROR_NO_SLOT_AVAILABLE`/errorId 2 без биллинга и без XADD; `0` =
  «выключено», а не «запретить всё».
- **Browser kill/reaper.** Одна сессия на задачу, kill дерева в `finally`,
  hard-timeout убивает дерево по label (`worker_browser/runner.py:140-181`,
  `tracking.py:102-126`), `reap_stale` на старте, профиль удаляется с
  ретраями (`supervisor.py:201-210`), GeoIP обязателен при прокси — host
  fallback запрещён (`provider.py:108-117`).
- **Прокси-креды в ошибках транспорта.** `_redact_proxy` (`curl.py:243-255`),
  pool логирует только `host:port` (`proxy/pool.py:109-120`).
- **Секреты в репозитории.** `.env` в `.gitignore` и `.dockerignore`,
  в git только `.env.example`; скан трекнутых файлов на
  `BEGIN … PRIVATE KEY`, `AIza`, `sk-`, `ghp_`, `AKIA`, `xox[baprs]-` — 0
  совпадений; оба образа работают non-root (`infra/docker/*.Dockerfile`).
- **TLS-периметр.** Caddy: ACME/внутренний CA, HSTS, `nosniff`, `DENY`,
  `Referrer-Policy: no-referrer`, лимит тела 40 МБ, gateway без host-порта.
- **Math-капча.** `simpleeval` с пустыми `names`/`functions` и regex
  `^[\d+\-*/().]+$`, `**` запрещён — RCE нет (`solvers/ocr/math_image.py`).
- **Опасные примитивы.** `pickle`/`marshal`/`yaml.load`/`shell=True` в
  `src` отсутствуют (grep).
- **Pingback не преследует редиректы** (httpx default), тело — только
  проекция решения.

## 3. Бэклог по приоритету

1. RT-06: проверка редиректов + redaction URL в контекстах ошибок транспорта.
2. RT-05: `page.route`-хук в browser lane (все request URL через netguard).
3. RT-07: сети/egress, IMDSv2, убрать дефолтные пароли из compose-дефолтов.
4. RT-08: эфемерный Redis для task-state или шифрование тома.
5. RT-12: middleware лимита тела на gateway.
6. RT-09: per-instance префикс профилей в browser lane.
7. RT-14, RT-15, RT-16, RT-13 — мелкие правки одним PR.
8. RT-D1: обновить `SECURITY.md`/`operations.md`/`architecture.md`.

## 4. Как воспроизвести проверки

```powershell
$env:PYTHONPATH='<worktree>/src'
D:/captcha/.venv/Scripts/python.exe -m pytest -q
D:/captcha/.venv/Scripts/python.exe -m ruff check src tests
D:/captcha/.venv/Scripts/python.exe -m pytest tests/gateway/test_url_guard.py -v
D:/captcha/.venv/Scripts/python.exe -m pytest tests/adapters/test_pingback_ssrf.py -v
```

Ручная проверка фикса (без сети, ASGI-клиент из теста): `POST /createTask` с
`websiteURL=http://169.254.169.254/latest/meta-data/` → `errorId 21`,
`ERROR_BAD_REQUEST`, баланс не меняется, в stream ничего не уходит.
`POST /in.php` с `pageurl=http://127.0.0.1/` → `ERROR_BAD_PARAMETERS`.

## 5. Изменённые файлы

```
src/captcha/adapters/netguard.py                     ensure_public_url_shape + _shape_check
src/captcha/adapters/pingback.py                     guard + safe_url
src/captcha/domain/redaction.py                      redact_dsn
src/captcha/services/gateway/url_guard.py            NEW  (validate_client_urls)
src/captcha/services/gateway/routes/tasks.py         guard before charge
src/captcha/services/gateway/routes/legacy.py        guard before charge
src/captcha/services/gateway/app.py                  redact_dsn in startup log
src/captcha/services/worker_http/worker.py           safe_url in pingback logs
src/captcha/services/worker_http/cli.py              redact_dsn
src/captcha/services/worker_browser/worker.py        redact_dsn
src/captcha/services/worker_cv/worker.py             redact_dsn
src/captcha/services/worker_protocol/worker.py       redact_dsn
src/captcha/solvers/ocr/_llm.py                      refuse override without LLM_ENDPOINT

tests/gateway/test_url_guard.py                      NEW  50
tests/adapters/test_pingback_ssrf.py                 NEW  11
tests/adapters/test_netguard_shape.py                NEW  19
tests/domain/test_redaction_dsn.py                   NEW  10
tests/solvers/ocr/test_llm_endpoint_policy.py        NEW   5

docs/redteam-report.md                               NEW (этот файл)
```

Ветка: `feat/redteam` (worktree `D:/wt/captcha-redteam`). Merge не делался.
