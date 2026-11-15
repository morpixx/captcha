# Runbook: castle

## 1. Snapshot

- **captcha type:** `CaptchaType.CASTLE` (`castle`) — Castle.io: `castle_request_token` минтится только на клиенте, живёт 120 с и валиден ровно на один запрос.
- **antibot family:** `AntibotEngine.CASTLE` (builder — `engines/castle/builder.py`).
- **lane:** `http`, **mode:** `both`, `needs_js_runtime=True`.
- **tier:** 3 — `experimental=True`; SDK изучен по публичной документации, живой прогон не снят.
- **code:** `solvers/antibot/castle/{solver,endpoints,payload,page}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | страница, где крутится SDK (алиасы `websiteUrl`, `pageurl`, `page_url`, `url`) |
| `publishable_key` / `pk` / `castle_pk` | да | публичный ключ `pk_...` |
| `castle_request_token` / `request_token` / `token` | нет | уже готовый токен: возвращается без пере-минта |
| `challenge_js` / `castle_js` / `sdk_js` / `script` | нет | исходник `castle.js` для capture-пути |
| `castle_js_url` / `sdk_url` / `script_url` | нет | откуда грузить SDK в браузере; по умолчанию `https://cdn.castle.io/v1/castle.js` |
| `storage` | нет | storage-объект страницы для билдера |
| `timeout_s` | нет | бюджет, по умолчанию 20 |

```json
{"type": "castle", "websiteURL": "https://target.example/login", "publishable_key": "pk_live_…"}
```

## 3. Endpoints / flow

Своего API у минта нет: `castle.js` собирает токен из device/behavior-сигналов **без сетевого вызова** (`endpoints.py`, документация SDK). Единственный зафиксированный сетевой адрес — легаси-мониторинг `https://d.castle.io/v1/c/mon`, который текущий SDK не вызывает.

1. **Pass-through:** есть готовый токен (`supplied_token`) → `page.validate_token` (не пусто, не эхо `pk`, длина 20…16384, без пробелов, charset `[A-Za-z0-9+/=_.:|~-]`) → возврат как есть. Ничего не пере-минтится.
2. **JS capture:** есть исходник SDK → билдер собирает программу, `_runtime.run_script` её исполняет, токен ищется в storage, cookies и телах перехваченных запросов (`page.token_from_request`). Пусто → `ERR_CHALLENGE_EXPIRED`, который солвер **переводит в `ERR_NEEDS_BROWSER`** — бандлу нужен реальный event loop.
3. **Browser lane:** навигация на страницу, `page.drive` конфигурирует SDK с `publishable_key`, вызывает `createRequestToken()`, возвращает токен и `__cuid`.

## 4. Solution keys

`SolutionKind.DATA_NEST` → `data: {"tokens": [<token>]}` (`domain/solution.py` для `CASTLE`: `tokens`, иначе список из `token`). `extra`: `userAgent`, `headers: {"User-Agent": ua}`, `cuid` (только из браузерного лейна).

```json
{"data": {"tokens": ["<castle_request_token>"]}, "userAgent": "<ua>", "cuid": "__cuid…"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `requires the publishable key`, `needs the page the SDK runs on` | нет `pk`/URL | требовать оба поля |
| `ERR_NEEDS_BROWSER` | `the token is minted client-side and needs a page` | нет SDK-исходника/рантайма | прислать `challenge_js` или принять браузерный лейн |
| `ERR_NEEDS_BROWSER` | `the SDK produced no token in the JS runtime` | бандлу нужен event loop | браузерный лейн |
| `ERR_CHALLENGE_EXPIRED` | `page minted no token` | виджет не отдал токен (таймаут/баннер) | новый раунд, свежий профиль |
| `ERR_UNSOLVABLE` | `castle builder returned no capture program` | билдер отдал не строку | проверить `challenge_js` |
| `ERR_CONFIG` | `SolverContext missing required fields` | воркер без sensors/js_runtime | см. §7 |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/antibot/test_antibot_solvers.py` (Castle: pass-through, отсутствие pk, отсутствие SDK, capture-путь, эскалация).
- Сборка программы захвата: `tests/engines/test_engine_builders.py`.
- Канареечной фикстуры нет: мок-токен `test|device:…` валиден только для тестов клиента.
- Кейс (не добавлен):

```json
{"name": "castle-live", "clientKey": "<canary-key>",
 "task": {"type": "castle", "websiteURL": "https://<host>/login", "publishable_key": "pk_live_..."},
 "expect": {"status": "ready", "solution_contains": ["data", "tokens"]}}
```

## 7. FILL / live-риски

1. Токен одноразовый и живёт 120 с: клиент обязан потратить его на тот же запрос, иначе раунд пропал. Это свойство вендора, а не бага.
2. Живого сайта нет: имена storage-ключей (`__cuid`) и формат мока взяты из публичной документации; `page.drive` на живом тенанте не проверялся.
3. Pass-through не проверяет подпись токена — только форму: подделанный токен вернётся клиенту как «решено».
4. Browser-лейн возвращает `cuid`, HTTP-путь — нет; клиент, которому нужен device id в HTTP-пути, его не получит.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_antibot_solvers.py tests/engines/test_engine_builders.py
uv run ruff check src/captcha/solvers/antibot/castle src/captcha/engines/castle
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/castle/solver.py), [payload.py](../src/captcha/solvers/antibot/castle/payload.py), [page.py](../src/captcha/solvers/antibot/castle/page.py), [endpoints.py](../src/captcha/solvers/antibot/castle/endpoints.py)
- builder: [builder.py](../src/captcha/engines/castle/builder.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
