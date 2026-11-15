# Runbook: tspd

## 1. Snapshot

- **captcha type:** `CaptchaType.TSPD` (`tspd`) — F5 Shape (BIG-IP Bot Defense / ASM proactive bot defense): VM минтит `TSPD_101` или `TS01<hex>`.
- **antibot family:** `AntibotEngine.TSPD` (builder — `engines/tspd/builder.py`).
- **lane:** `http`, **mode:** `both`, `needs_js_runtime=True`.
- **tier:** 3 — `experimental=True`; живого тенанта нет.
- **code:** `solvers/antibot/tspd/{solver,endpoints,cookies}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` / `pageurl` / `url` | да | защищённая страница; из неё origin и Referer |
| `challenge_js` / `tspd_script` / `script` / `main_js` | да* | исходник Shape-VM; без него HTTP-путь сразу эскалирует |
| `tspd_url` / `endpoint` / `sensor_url` | нет | куда реплеить сенсорный POST (по умолчанию — сам сайт) |
| `cookies` | нет | jar прошлого раунда |
| `userAgent` / `ua` | нет | UA сессии |
| `timeout_s` | нет | бюджет, по умолчанию 20 |

`*` — обязателен для HTTP-пути; браузерный лейн работает без него.

```json
{"type": "tspd", "websiteURL": "https://target.example/", "challenge_js": "<исходник main.min.js>"}
```

## 3. Endpoints / flow

Константного URL нет: Shape отдаётся с клиентского префикса на самом origin (`/TSPD/…`, `/__TS/…`, `…/main.min.js`), имя cookie тоже per-deployment.

1. Нет скрипта → сразу `ERR_NEEDS_BROWSER` (VM — генератор payload-а, без него собирать нечего).
2. Билдер собирает программу захвата; `_runtime.run_script` исполняет её в JS-рантайме воркера.
3. `capture.parse` отдаёт cookies и перехваченные запросы; `cookies.replay` шлёт сенсорный POST — только на сайт или на `tspd_url`, чужие хосты отбрасываются.
4. `cookies.require` ищет cookie по префиксам `TSPD`, `TS` (длинный префикс первым, чтобы `TSPD_101` не перекрывался `TS`).
5. Ответ: jar из **всех** `TS*`-cookie раунда.

**Browser lane:** навигация на страницу, ожидание cookie по префиксам, затем чтение всего jar (деплой ставит семейство, а не одну cookie).

## 4. Solution keys

`SolutionKind.COOKIE_ENGINE` → `cookies`, `domains: {host: {cookies}}`, `userAgent`. Для TSPD `domain/solution.py` дополнительно кладёт капитализированную пару `Domains: {host: {Cookies}}` — так её читает эталонный клиент.

```json
{"cookies": {"TSPD_101": "…"}, "domains": {"target.example": {"cookies": {"TSPD_101": "…"}}},
 "Domains": {"target.example": {"Cookies": {"TSPD_101": "…"}}}, "userAgent": "<ua>"}
```

`token` = значение найденной `TS*`-cookie.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `needs the protected page URL`, `builder returned no capture program` | нет URL / билдер отдал не строку | требовать `websiteURL`, сверить скрипт |
| `ERR_NEEDS_BROWSER` | `the Shape VM is the payload generator and its script is missing` | нет `challenge_js` | прислать скрипт или принять браузерный лейн |
| `ERR_CHALLENGE_EXPIRED` | `the round produced no TS cookie` | VM отработала, cookie не выставлена | новый раунд/профиль |
| `ERR_JS_RUNTIME` | ошибка `ctx.js_runtime` | рантайм упал | см. §7, проверить лейн-рантайм |
| `ERR_CONFIG` | `SolverContext missing required fields` | воркер без sensors | см. §7 |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/antibot/test_antibot_solvers.py` (TSPD: нет URL, нет скрипта, capture, отсутствие cookie), сборка — `tests/engines/test_engine_builders.py`.
- Живых дампов Shape нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "tspd-live", "clientKey": "<canary-key>",
 "task": {"type": "tspd", "websiteURL": "https://<host>/", "challenge_js": "<main.min.js>"},
 "expect": {"status": "ready", "solution_contains": ["cookies", "Domains"]}}
```

## 7. FILL / live-риски

1. Живого тенанта нет; формат сенсорного POST берётся из того, что VM сама попыталась отправить. Pass rate не измерен.
2. `TSPD_101` — дефолтное имя, не гарантия: клиент, который ищет ровно эту cookie, на переименованном деплое её не найдёт (в ответе будут все `TS*`).
3. Реплей идёт только на сайт/`tspd_url`; если живой деплой шлёт сенсор на отдельный CDN-хост, а клиент не передал `tspd_url`, POST не уйдёт и раунд закончится `ERR_CHALLENGE_EXPIRED`.
4. Скрипт должен быть именно Shape-VM: `/TSPD/`, `/__TS/`, `main.min.js` — эвристика распознавания (`TSPD_SCRIPT_HINTS`) в HTTP-пути не применяется, он доверяет payload-у.
5. Сколько живых деплоев отдают `TS01<hex>` вместо `TSPD_101` — не проверено.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_antibot_solvers.py tests/engines/test_engine_builders.py
uv run ruff check src/captcha/solvers/antibot/tspd src/captcha/engines/tspd
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/tspd/solver.py), [cookies.py](../src/captcha/solvers/antibot/tspd/cookies.py), [endpoints.py](../src/captcha/solvers/antibot/tspd/endpoints.py)
- builder: [builder.py](../src/captcha/engines/tspd/builder.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
