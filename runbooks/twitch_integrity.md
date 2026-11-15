# Runbook: twitch_integrity

## 1. Snapshot

- **captcha type:** `CaptchaType.TWITCH_INTEGRITY` (`twitch_integrity`) — Twitch Client-Integrity JWT для GQL: `POST https://gql.twitch.tv/integrity`.
- **antibot family:** `AntibotEngine.TWITCH` (builder — `engines/twitch/builder.py`, внутри KPSDK-bootstrap и шим).
- **lane:** `http`, **mode:** `both`, `needs_js_runtime=True`.
- **tier:** 3 — `experimental=True`; живой прогон не снят.
- **code:** `solvers/antibot/twitch_integrity/{solver,payload,wire,browser}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `client_integrity` / `clientIntegrity` / `integrity_token` / `integrity` | нет | готовый JWT: короткий путь, сеть не нужна |
| `challenge_js` / `p_script` / `kpsdk_js` | нет | инлайн KPSDK-bootstrap |
| `p_script_url` / `script_url` / `challenge_js_url` / `kpsdk_url` | нет | откуда скачать bootstrap; иначе `KPSDK_SCRIPT_URL` |
| `device_id` / `deviceId` / `x-device-id` | нет* | id устройства; без него заголовок не уходит |
| `client_id` / `clientId` | нет | по умолчанию `WEB_CLIENT_ID` из билдера |
| `client_session_id`, `client_version`, `client_request_id` | нет | заголовки фронтенда |
| `oauth` / `oauth_token` / `access_token` / `authorization` | нет | префикс `OAuth ` срезается |
| `headers` | нет | дополнительные заголовки (lowercase) |
| `websiteURL` / `pageurl` | нет | страница для браузерного лейна; по умолчанию `DEFAULT_PAGE_URL` |
| `timestamp_ms`, `js_timeout_ms` | нет | входы билдера; JS-бюджет по умолчанию 20000 |

`*` — эндпоинт не выведет device id сам; без него запрос уйдёт «безымянным».

```json
{"type": "twitch_integrity", "device_id": "<uuid>", "client_id": "kimne78kx3ncx6brgo4mv6wki5h1ko"}
```

## 3. Endpoints / flow

`engines/twitch/builder.py`: `INTEGRITY_URL = https://gql.twitch.tv/integrity`, `INTEGRITY_PREFIX = "v4.public."`, `SIGNATURE_BYTES = 64`.

1. Готовый `client_integrity` → сразу ответ (claims декодируются, bot-флаг проверяется).
2. Иначе нужны bootstrap (payload или скачивание `script_url`) и `ctx.js_runtime`; нет рантайма → `ERR_NEEDS_BROWSER`.
3. Билдер собирает `{script, globals, headers, url}`; `exec_script` гоняет KPSDK, который патчит `fetch`; `wire.capture_headers` снимает `x-kpsdk-*` и мёржит с базовыми.
4. `POST /integrity` пустым телом с этими заголовками: 401/403 → `ERR_SENSOR` (отпечаток/заголовки), 5xx → `ERR_SENSOR` (эндпоинт недоступен), прочие 4xx → `ERR_CHALLENGE_EXPIRED`.
5. `wire.parse_response` → `(token, expiration)`; `wire.flagged(claims)` на bot-признаки → `ERR_SENSOR`.

**Browser lane:** открывает плеер, `page.integrity_js(headers)` делает `fetch` со страницы и возвращает JWT.

## 4. Solution keys

`SolutionKind.TOKEN` → `token`, `userAgent`. `extra`: `userAgent`, `deviceId`, `expiration` (unix-секунды, если сервер их отдал).

```json
{"token": "v4.public.…", "userAgent": "<ua>", "deviceId": "<uuid>", "expiration": 1763000000}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_SENSOR` | `/integrity rejected the device or headers` | 401/403: отпечаток не сошёлся | другой профиль/прокси, свежий bootstrap |
| `ERR_SENSOR` | `flagged the integrity token as bot traffic` | claims содержат bot-признак | сменить профиль, не переиспользовать токен |
| `ERR_SENSOR` | `/integrity is unavailable`, `bootstrap fetch failed` | 5xx/не скачался p.js | ретрай, проверить URL |
| `ERR_CHALLENGE_EXPIRED` | `refused the challenge` (4xx) | протухший bootstrap | новый p.js |
| `ERR_NEEDS_BROWSER` | `needs the KPSDK bootstrap` | нет bootstrap и нет рантайма | прислать `challenge_js`/URL или браузерный лейн |
| `ERR_CONFIG` | `browser lane needs ctx.browser` | браузерный вызов без провайдера | см. §7 |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/antibot/test_twitch_integrity.py` (готовый токен, fetch bootstrap, 401/403/5xx, bot-флаг, browser-путь), драйвер — `tests/engines/test_twitch_driver.py`.
- Живых JWT в репозитории нет (короткоживущие). Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "twitch_integrity-live", "clientKey": "<canary-key>",
 "task": {"type": "twitch_integrity", "device_id": "<uuid>"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. Живой Twitch не прогонялся: KPSDK-шим детерминированный, а не «настоящий» — сколько запросов он проходит на живом фронте, не измерено.
2. `expiration` кладётся в `extra` как есть; клиент обязан сам решить, когда обновлять. TTL на живом сервере не замерен.
3. `WEB_CLIENT_ID` зашит в билдер: смена публичного client-id ломает все вызовы (это будет видно как 4xx/401).
4. `deviceId` не генерируется — приходит только из payload; без него есть риск 401/403, и это `ERR_SENSOR`, а не «пустой токен».
5. Браузерный лейн требует `ctx.browser`; на http-воркере его нет, поэтому `preferred_mode=BROWSER` — единственный вход.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_twitch_integrity.py tests/engines/test_twitch_driver.py
uv run ruff check src/captcha/solvers/antibot/twitch_integrity src/captcha/engines/twitch
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/twitch_integrity/solver.py), [payload.py](../src/captcha/solvers/antibot/twitch_integrity/payload.py), [wire.py](../src/captcha/solvers/antibot/twitch_integrity/wire.py), [browser.py](../src/captcha/solvers/antibot/twitch_integrity/browser.py)
- builder: [builder.py](../src/captcha/engines/twitch/builder.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
