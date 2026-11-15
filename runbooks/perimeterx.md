# Runbook: perimeterx

## 1. Snapshot

- **captcha type:** `CaptchaType.PERIMETERX` (`perimeterx`) — PerimeterX / HUMAN Security: сенсорный раунд + интерстишиал `px3`; ответ = jar `_px*`, где `_pxhd` — вердикт.
- **antibot family:** `AntibotEngine.PERIMETERX` (builder — `engines/perimeterx/builder.py`, кодек `ob` — `engines/perimeterx/codec.py`).
- **lane:** `http`, **mode:** `both` (`solve_http` + `solve_browser`), `needs_js_runtime=True`.
- **tier:** 3 — `experimental=True`; разбор `docs/reverse-2026.md` §7, живой тенант не снят.
- **code:** `solvers/antibot/perimeterx/{solver,endpoints,payload,response,press}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `app_id` | да | `PX...`; алиасы `px_app_id`, `pxAppId`, `px_appId` |
| `websiteURL` | да | страница, где снят сенсор (алиасы `websiteUrl`, `pageurl`, `url`) |
| `collector_url` / `collector` / `px_collector_url` | нет | явный collector вместо `collector-<appId>.px-cloud.net` |
| `userAgent` / `user_agent` / `ua` | нет | UA сессии |
| `cookies` | нет | jar прошлого раунда; плюс отдельные `_px`, `_pxhd`, `_pxvid` |
| `challenge_js` / `bundle_js` / `press_js` / `script` | нет | исходник press-бандла: его наличие переключает на press-путь |
| `tag` / `mobile` | нет | `tag` коллектора, по умолчанию `web` (`mobile` → `mobile`) |
| `uuid` / `vid` / `_pxvid`, `sid`, `pc_len` | нет | входы билдера |
| `js_timeout_ms` | нет | бюджет JS, по умолчанию 15000 |
| `timeout_s` | нет | бюджет ожидания cookie в браузере, по умолчанию 20 |

```json
{"type": "perimeterx", "app_id": "PXabc123", "websiteURL": "https://target.example/checkout",
 "userAgent": "<ua>", "cookies": {"_pxvid": "…"}}
```

## 3. Endpoints / flow

`endpoints.py`: collector `https://collector-<appId>.px-cloud.net/api/v2/collector` (старый хост `collector-<appId>.perimeterx.net`), интерстишиал `https://captcha.px-cloud.net/<appId>`, submit `.../<appId>/api/v2/captcha`.

**Sensorless collector (HTTP-first):**

1. `engines/perimeterx/builder.py` собирает форму (`appId, tag, ft, seq, en, uuid, vid, cts, sid, pc, payload`).
2. `POST` form-encoded на collector с `User-Agent`, `Referer: <page>`, `Origin`; cookies прошлого раунда прикладываются.
3. Статус 0 или ≥ 400 → `ERR_SENSOR`. Иначе `Set-Cookie` + `ob`-блоб (`response.ob`) декодируются в `_px*`, jar мёржится.

**Press / bundle (js-runtime):**

1. Тот же билдер отдаёт не форму, а программу захвата (`challenge_js`).
2. `press.run` исполняет её под capture-harness и реплеит вызов коллектора **только** на `px-cloud.net` / `perimeterx.net` или на сам сайт — чужой скрипт не может увести транспорт на третий хост.

**Browser lane:** навигация на `websiteURL`, `wait_for_cookie` по префиксам `_px`/`pxcts`, затем читается весь jar страницы (`_pxhd` обычно появляется позже `_pxvid`).

## 4. Solution keys

`SolutionKind.COOKIE_ENGINE` → `cookies`, `domains: {host: {cookies}}`, `userAgent` (`domain/solution.py`). `extra` содержит `cookies` (только `_px*`), `userAgent`, `domain`, `headers`.

```json
{"cookies": {"_pxhd": "…", "_px": "…"}, "domains": {"target.example": {"cookies": {"_pxhd": "…"}}},
 "userAgent": "<ua>"}
```

`token` = `_pxhd`, иначе `_px`/`_px2`/`_px3` (`endpoints.PX_PRIMARY_COOKIES`).

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `requires the site's appId`, `needs the page the sensor was taken on` | нет `app_id`/`websiteURL` | требовать оба поля у клиента |
| `ERR_SENSOR` | `collector rejected the sensor body` | форма не прошла (дрейф схемы) | регенерировать билдер, сверить `status`/`app_id` из контекста |
| `ERR_CHALLENGE_EXPIRED` | `no PerimeterX cookie obtained` / `browser session never set…` | раунд протух или страница не отдала cookie | свежий профиль/прокси, новый раунд |
| `ERR_NEEDS_BROWSER` | эскалация | нет `ctx.js_runtime`/`ctx.sensors` при script-payload | дать JS-рантайм или браузерный лейн |
| `ERR_CONFIG` | `SolverContext missing required fields` | воркер без http/sensors | см. §7 |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/antibot/test_antibot_solvers.py` (класс PX: нет app_id, нет страницы, collector override, press-путь, `ERR_CHALLENGE_EXPIRED`).
- Кодек `ob`: `tests/engines/test_perimeterx_codec.py`; сборка формы — `tests/engines/test_engine_builders.py`.
- Живых дампов коллектора в репозитории нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "perimeterx-live", "clientKey": "<canary-key>",
 "task": {"type": "perimeterx", "app_id": "PX...", "websiteURL": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["cookies", "_pxhd"]}}
```

## 7. FILL / live-риски

1. Живого тенанта нет: форма сенсора собрана по разбору, не по живому collector-ответу. Pass rate не измерен.
2. `ob`-кодек понимает `~~~~`-сегменты одного поколения; при смене формата cookies не испекутся и раунд станет `ERR_CHALLENGE_EXPIRED`, а не `ERR_SENSOR`.
3. Браузерный лейн ждёт **первую** cookie семейства; полный jar читается сразу после, но `_pxhd` при медленном интерстишиале может появиться позже таймаута.
4. Имена cookie per-tenant не ключуются на конкретное имя, кроме `_px*`/pxcts.
5. `press`-путь требует capture-harness и реплеит только разрешённые хосты; сколько живых деплоев реально отдают бандл — не проверено.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_antibot_solvers.py tests/engines/test_perimeterx_codec.py tests/engines/test_engine_builders.py
uv run ruff check src/captcha/solvers/antibot/perimeterx src/captcha/engines/perimeterx
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/perimeterx/solver.py), [payload.py](../src/captcha/solvers/antibot/perimeterx/payload.py), [response.py](../src/captcha/solvers/antibot/perimeterx/response.py), [endpoints.py](../src/captcha/solvers/antibot/perimeterx/endpoints.py), [press.py](../src/captcha/solvers/antibot/perimeterx/press.py)
- builder: [builder.py](../src/captcha/engines/perimeterx/builder.py), кодек: [codec.py](../src/captcha/engines/perimeterx/codec.py)
- разбор: [docs/reverse-2026.md](../docs/reverse-2026.md) §7
- протокол: [docs/protocol.md](../docs/protocol.md)
