# Runbook: imperva

## 1. Snapshot

- **captcha type:** `CaptchaType.IMPERVA` (`imperva`) — Imperva / Incapsula: reese84-конверт или `___utmvc`; ответ = jar с `reese84` и ротацией `incap_ses_*`/`visid_incap_*`.
- **antibot family:** `AntibotEngine.IMPERVA` (builder — `engines/imperva/builder.py`).
- **lane:** `http`, **mode:** `both`, `needs_js_runtime=True`.
- **tier:** 3 — `experimental=True`; живой тенант не снят.
- **code:** `solvers/antibot/imperva/{solver,endpoints,payload,response,script}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` / `origin` | да | защищённый origin; из URL берётся только `scheme://host` |
| `reeseScriptURL` / `reese_script_url` / `script_url` | нет | URL динамического reese-скрипта (у Capsolver — обязательное поле) |
| `challenge_js` / `reese_script` / `reeseScript` / `script` / `main_js` | нет | исходник скрипта: переключает на capture-путь |
| `cookies` | нет | jar прошлого раунда; плюс отдельные `reese84`, `___utmvc` |
| `userAgent` / `user_agent` / `ua` | нет | UA сессии |
| `nonce` | нет | значение `e` в `/_Incapsula_Resource`; иначе случайные 4 байта hex |
| `version` | нет | версия reese84 для билдера |
| `js_timeout_ms` | нет | бюджет JS, по умолчанию 15000 |
| `timeout_s` | нет | бюджет ожидания cookie, по умолчанию 20 |

```json
{"type": "imperva", "websiteURL": "https://target.example/", "reeseScriptURL": "https://target.example/_Incapsula_Resource?SWJIYLWA=719d34d31c8e3a6e6fffd425f7e032f3",
 "userAgent": "<ua>"}
```

## 3. Endpoints / flow

`endpoints.py`: `POST <origin>/_Incapsula_Resource?SWKMTFSR=1&e=<nonce>`; cookie-семейства `visid_incap`, `incap_ses`, `nlbi`, `reese84`, `___utmvc` (префиксное совпадение — Imperva суффиксирует `incap_ses_<digits>`).

**reese84-конверт (HTTP-first):**

1. `engines/imperva/builder.py` собирает `{"solution": {...}, "old_token": …, "error": null, "performance": {...}}`.
2. `POST` JSON на resource-URL с `User-Agent`, `Referer`, `Origin`; cookies прошлого раунда прикладываются.
3. Статус 0 или ≥ 400 → `ERR_SENSOR`; иначе `Set-Cookie` мёржится в jar.

**Capture (js-runtime):** если в payload есть исходник скрипта, билдер отдаёт программу захвата, `script.run` исполняет её и реплеит сенсорный POST — только на сайт или на хосты Imperva (`incapsula.com`, `impervadns.net`, `imperva.com`).

**Browser lane:** навигация на origin, ожидание cookie по префиксам, затем чтение всего jar (семейство ротируется вместе).

## 4. Solution keys

`SolutionKind.COOKIE_ENGINE` + спец-поле Imperva: `utmvc` (`domain/solution.py` кладёт `cookies["___utmvc"]` отдельным ключом, как это делает CapSolver). Остальное: `cookies`, `domains: {host: {cookies}}`, `userAgent`.

```json
{"cookies": {"reese84": "…", "incap_ses_123_456": "…"}, "domains": {"target.example": {"cookies": {"reese84": "…"}}},
 "utmvc": "<___utmvc>", "userAgent": "<ua>"}
```

`token` = `reese84`, иначе первая cookie семейства.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `needs the protected origin` | нет `websiteURL`/`origin` | требовать URL страницы |
| `ERR_SENSOR` | `imperva rejected the reese84 envelope` | конверт не принят (дрейф схемы) | сверить `solution_keys`/версию из контекста, регенерировать |
| `ERR_CHALLENGE_EXPIRED` | `no Imperva cookie obtained` | раунд протух, cookie не выставлена | новый раунд, свежий профиль |
| `ERR_NEEDS_BROWSER` | эскалация | нет JS-рантайма при script-payload | дать рантайм или браузерный лейн |
| `ERR_CONFIG` | `SolverContext missing required fields` | воркер без http/sensors | см. §7 |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/antibot/test_antibot_solvers.py` (Imperva: origin-нормализация, конверт, capture-путь, browser fallback), сборка — `tests/engines/test_engine_builders.py`.
- Живых дампов `/_Incapsula_Resource` нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "imperva-live", "clientKey": "<canary-key>",
 "task": {"type": "imperva", "websiteURL": "https://<host>/", "reeseScriptURL": "<url со страницы>"},
 "expect": {"status": "ready", "solution_contains": ["cookies", "reese84"]}}
```

## 7. FILL / live-риски

1. Живого тенанта нет; `solution`-объект собран по разбору. Pass rate не измерен.
2. `nonce` по умолчанию случайный: если сервер связывает его с сессией, эскалации будут регулярными — не проверено.
3. `reeseScriptURL` сам не скачивается: клиент обязан прислать либо URL (для билдера), либо исходник. Скачивания URL солвер не делает.
4. Браузерный лейн ждёт первую cookie семейства; при медленном интерстишиале `reese84` может появиться позже и вернуться `ERR_CHALLENGE_EXPIRED`.
5. Поле `version` уходит в билдер как есть; какая версия у живого деплоя — не проверено.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_antibot_solvers.py tests/engines/test_engine_builders.py
uv run ruff check src/captcha/solvers/antibot/imperva src/captcha/engines/imperva
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/imperva/solver.py), [payload.py](../src/captcha/solvers/antibot/imperva/payload.py), [response.py](../src/captcha/solvers/antibot/imperva/response.py), [script.py](../src/captcha/solvers/antibot/imperva/script.py), [endpoints.py](../src/captcha/solvers/antibot/imperva/endpoints.py)
- builder: [builder.py](../src/captcha/engines/imperva/builder.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
