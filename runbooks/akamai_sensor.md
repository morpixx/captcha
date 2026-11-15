# Runbook: akamai_sensor

## 1. Snapshot

- **captcha type:** `CaptchaType.AKAMAI_SENSOR` — Akamai Bot Manager, классический `sensor_data` POST; ответ = cookie-jar с `_abck`.
- **antibot family:** `AntibotEngine.AKAMAI` (builder — `engines/akamai/builder.py`).
- **lane:** `http`, **mode:** `http` (`needs_js_runtime=True`).
- **tier:** 3 — `experimental=True`: протокол по публичным разборам 2026, живого тенанта нет.
- **code:** `solvers/antibot/akamai/{solver,wire,abck,endpoints}.py`.

## 2. Payload

Payload-режим (`docs/protocol.md`, «Payload mode») — клиент приносит сессию; bootstrap GET только
оппортунистический.

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | защищённая страница; из неё берутся origin и referer |
| `cookies` | нет | jar прошлого раунда: dict, строка `a=b; c=d` или `[{name,value}]` |
| `userAgent` | нет* | UA сессии; *без него и без fingerprint-store — `ERR_CONFIG` |
| `sensor_js` / `challenge_js` | нет | исходник тенантского сенсор-скрипта (тогда сеть не нужна) |
| `sensor_js_url` / `script_url` | нет | URL скрипта, если он известен |
| `akam_url` / `endpoint` | нет | переопределение endpoint POST |
| `sensor_encoder` | нет | `js` (дефолт) или `model` — второй не требует `bmak.js` |
| `sensor_format` / `abck_version` | нет | `form` vs JSON; `abck_version < 3` значит form |

```json
{"type": "akamai_sensor", "websiteURL": "https://target.example/", "userAgent": "<ua>",
 "cookies": {"_abck": "…", "bm_sz": "…"}, "sensor_js_url": "https://target.example/akam/13/…"}
```

## 3. Endpoints / flow

1. `GET <websiteURL>` — `Set-Cookie: bm_sz` (seed) и `_abck ~-1~` (ожидание). 403/429/451
   транспорт превращает в `ChallengeExpiredError` до чтения тела — это нормальное состояние
   защищённой страницы, поэтому провал bootstrap = заметка, а не провал задачи.
2. Сенсор-скрипт: `sensor_js` → `sensor_js_url` → поиск в HTML (маркеры `/akam/`, `/_sec/`,
   `bmak`). Нет скрипта — `ERR_UNSOLVABLE` с подсказкой, что прислать.
3. `engines/akamai/builder.py` прогоняет скрипт тенанта в V8-рантайме воркера за
   stub-браузером (`engines/akamai/harness.js`, `JS_TIMEOUT_MS = 8000`) и забирает ровно тот
   POST, который скрипт попытался отправить. Падение скрипта — `SensorError`, «правдоподобный»
   блоб не синтезируется.
4. `POST <target>` — `{"sensor_data": "<opaque>"}` (JSON) или form `sensor_data=`; target
   выбирается в порядке: `akam_url`, `endpoint`, endpoint из билдера, URL скрипта,
   `sensor_js_url`, bootstrap-URL.
5. До `MAX_SENSOR_POSTS = 3` попыток; после каждого — `_abck` перечитывается.
6. Вердикт — только хвост `_abck` (`solvers/antibot/akamai/abck.py`): `~-1~` не принято,
   `~0~-1~` промежуточно, `~0~` satisfied. `HTTP 428` = sec-cpt challenge.

## 4. Solution keys

`SolutionKind.COOKIE_ENGINE`: `cookies` (весь jar без пустых значений), `domains:
{host: {cookies}}`, `userAgent`. `token` = `_abck`, но в solution он не проецируется —
клиент читает cookie-форму. Cookie-значения не логируются (контракт §5).

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | «the tenant sensor script is required»; «no sensor endpoint discovered» | клиент не прислал скрипт, страница не отдала HTML | прислать `sensor_js`/`sensor_js_url` |
| `ERR_CONFIG` | «no user agent on the task or in the fingerprint store» | `_abck` привязан к UA | прислать `userAgent` |
| `ERR_CHALLENGE_EXPIRED` | «_abck never settled» / «sensor POST rejected by the edge» | 403/429/451, либо сенсор не принят | свежий профиль/IP; проверить форму тела |
| `ERR_CHALLENGE_EXPIRED` | «sec-cpt challenge (HTTP 428)» | второй слой (PoW/поведение) не реализован | не ретраить в стену, эскалировать владельцу |
| `ERR_SENSOR` | ошибка из builder (`JSRuntimeError`/`SensorError`) | скрипт не запустился / пустой ответ | `ctx.js_runtime`, размер скрипта (`MAX_SCRIPT_CHARS = 8e6`) |
| `ERR_PROXY` | проброс из транспорта | прокси отвалился | менять прокси, не payload |

## 6. Canary / фикстуры

- Юнит-реплей билдера: `tests/engines/test_akamai_builder.py`.
- Флоу: `tests/solvers/antibot/test_akamai_flow.py` (там же фейки транспорта и рантайма).
- Каталога `fixtures/` у движка нет — живых дампов сенсора в репозитории не лежит.
- Кейс (не добавлен; нужен тенант, который отдаёт страницу без 403):

```json
{"name": "akamai-sensor", "clientKey": "<canary-key>",
 "task": {"type": "akamai_sensor", "websiteURL": "https://<host>/", "userAgent": "<ua>"},
 "expect": {"status": "ready", "solution_contains": ["cookies", "domains", "userAgent"]}}
```

## 7. FILL / live-риски

1. `sec-cpt` (HTTP 428, PoW/поведенческий слой) не реализован: солвер честно сообщает маркеры
   и не притворяется.
2. `sensor_encoder="model"` (`engines/akamai/sensor.py`) — только размеченная модель двух
   документированных стадий v3; её точность не измерена.
3. Маркеры `_abck` — наблюдение трафика, не контракт вендора; `UNKNOWN` = «не решено».
4. В `docs/reverse-2026.md` раздела про Akamai нет (это отмечено в docstring солвера) — вся
   база знаний в коде.
5. Ни один живой тенант не проверялся: pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/engines/test_akamai_builder.py tests/solvers/antibot/test_akamai_flow.py
uv run ruff check src/captcha/solvers/antibot/akamai src/captcha/engines/akamai
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/akamai/solver.py),
  [abck.py](../src/captcha/solvers/antibot/akamai/abck.py),
  [wire.py](../src/captcha/solvers/antibot/akamai/wire.py),
  [endpoints.py](../src/captcha/solvers/antibot/akamai/endpoints.py)
- builder: [builder.py](../src/captcha/engines/akamai/builder.py), `sensor.py`
- протокол решения: [docs/protocol.md](../docs/protocol.md)
- канарейка: [runbooks/canary.md](canary.md)
