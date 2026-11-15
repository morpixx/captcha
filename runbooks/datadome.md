# Runbook: datadome (+ datadome_slider)

## 1. Snapshot

- **captcha types:** `CaptchaType.DATADOME_FULL` (`datadome`) и `CaptchaType.DATADOME_SLIDER` (`datadome_slider`) — один солвер, три пути, один ответ: cookie `datadome`.
- **antibot family:** `AntibotEngine.DATADOME` (builder — `engines/datadome/builder.py`, пока заглушка).
- **lane:** `http`, **mode:** `both` (`solve_browser` есть, но только по эскалации).
- **tier:** 3 — `experimental=True`; хосты сняты с дампов 2026-06…11, pass rate не измерен.
- **code:** `solvers/antibot/datadome/{solver,payload,response,tags,interstitial,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` (или `pageurl`/`url`) | да | страница; из неё `domain`, referer, origin |
| `ddjskey` / `ddk` / `sitekey` | нет | публичный ключ; при наличии идёт tags.js-путь |
| `cookies` | нет | jar: dict, строка или список |
| `datadome` | нет | cookie прошлого раунда — тогда ответ сразу |
| `cid`, `hsh` | нет | входы подписи tags.js |
| `signals` / `parsed_body` / `sensor` | нет | уже собранные сигналы устройства |
| `challenge_js` / `challenge_script` | нет | скрипт интерстишла для JS-рантайма |
| `js_timeout_ms` | нет | бюджет JS, дефолт 15000 |
| `timeout_s` | нет | бюджет ожидания cookie в браузере, дефолт 15.0 |
| `timestamp_ms`, `userAgent`, `domain`, `challenge_url` | нет | переопределения |

```json
{"type": "datadome", "websiteURL": "https://target.example/", "userAgent": "<ua>",
 "ddjskey": "<key>", "cookies": {"datadome": "…"}}
```

## 3. Endpoints / flow

Хосты — `endpoints.py`: `api-js.datadome.co/js/`, `geo.captcha-delivery.com/interstitial/`,
`geo.captcha-delivery.com/captcha/check`, CDN картинок `dd.prod.captcha-delivery.com`,
бандл `ct.captcha-delivery.com/interstitial.<version>.js`.

1. **Cookie уже в payload** — сразу `SolverResult` (самый дешёвый и самый надёжный путь).
2. **tags.js** (`DATADOME_FULL` + `ddjskey`): тело `jspl` собирает `tags.build_tags_body`
   (ddk, cid, hsh, href, UA, сигналы, `jsType`: `ch` для full, `cs` для slider, `timestamp_ms`),
   `POST https://api-js.datadome.co/js/`, cookie читается из `Set-Cookie` и/или тела.
3. **Device Check / interstitial** (`DATADOME_SLIDER` без cookie, или full без `ddjskey`):
   без `challenge_js` или без `ctx.js_runtime` — `NeedsBrowserError`; иначе скрипт
   выполняется в рантайме, cookie/заголовки берутся из его ответа.
4. **Браузерный лейн**: одна сессия через `ctx.browser`, навигация, ожидание cookie из jar.

## 4. Solution keys

`SolutionKind.COOKIE_ENGINE`: `cookies: {datadome: …}`, `domains: {host: {cookies}}`,
`userAgent` и `headers` (UA), если известны. `token` = значение cookie.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `websiteURL`/слишком длинный/не http(s); tags.js без UA | битый payload | проверить поля, `userAgent` |
| `ERR_SENSOR` | tags-эндпоинт вернул `status >= 400` | подпись/сигналы не приняты | свежий `hsh`/`cid`, другой профиль |
| `ERR_CHALLENGE_EXPIRED` | «no datadome cookie obtained» | раунд не дал cookie | повторить, сменить IP/UA |
| `ERR_NEEDS_BROWSER` | эскалация | нет `challenge_js` или JS-рантайма | прислать скрипт или браузерный лейн |
| `ERR_CONFIG` | `ctx.browser` is None в браузерном лейне | лейн без провайдера | проверить деплой |

## 6. Canary / фикстуры

- Реплей: `tests/solvers/antibot/fixtures/datadome/tags_ok.json`,
  `tests/solvers/antibot/fixtures/datadome/interstitial_ok.json` (+ `README.md` там же).
- Тесты: `tests/solvers/antibot/test_datadome.py`.
- Кейс (не добавлен):

```json
{"name": "datadome-live", "clientKey": "<canary-key>",
 "task": {"type": "datadome", "websiteURL": "https://<host>/", "cookies": {"datadome": "<cookie>"}},
 "expect": {"status": "ready", "solution_contains": ["cookies", "domains", "userAgent"]}}
```

## 7. FILL / live-риски

1. `engines/datadome/builder.py` — **заглушка**: поля `ddk`, `ua`, `hsh`, `responsePage`
   пустые с `# FILL`, сборка заканчивается `SensorError`. Путь tags.js обходит билдер
   (`tags.py` собирает тело сам), но «полная» модель сигналов отсутствует.
2. Подпись сигналов (rolling key из `cid` + `hsh`) не реализована — только передача готовых
   `signals` клиентом.
3. Капча-слайдер (`/captcha/check`, `dd-tc`/`dd-tr`) в HTTP-пути не собирается: слайдер-тип
   идёт через интерстишл или браузер.
4. Pass rate не измерен; живых инцидентов нет.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_datadome.py
uv run ruff check src/captcha/solvers/antibot/datadome src/captcha/engines/datadome
```

## 9. Incident log

- 2026-11-15 — runbook создан — тип остаётся `experimental`; билдер всё ещё заглушка.

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/datadome/solver.py),
  [payload.py](../src/captcha/solvers/antibot/datadome/payload.py),
  [response.py](../src/captcha/solvers/antibot/datadome/response.py),
  [endpoints.py](../src/captcha/solvers/antibot/datadome/endpoints.py)
- builder: [builder.py](../src/captcha/engines/datadome/builder.py)
- разбор: `docs/reverse-2026.md` §5
- протокол: [docs/protocol.md](../docs/protocol.md)
