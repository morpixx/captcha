# Runbook: hunt

## 1. Snapshot

- **captcha type:** `CaptchaType.HUNT` (`hunt`) — Hunt interactive captcha: серверный API (init/verify) или виджет в браузере; ответ вложен в `data.token`.
- **antibot family:** нет. Хост и JWT выдаёт интегратор, поэтому эндпоинты — типовые пути, а не константы.
- **lane:** `http`, **mode:** `both`, `browser=True`.
- **tier:** 3 — `experimental=True`; живого интегратора нет.
- **code:** `solvers/interactive/hunt/{solver,wire,endpoints,browser}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `host` | да* | API-хост интегратора; алиасы `api_host`, `apiHost`; схема дописывается |
| `session_jwt` | да* | JWT сессии страницы; алиасы `sessionJwt`, `jwt` (уходит как `Bearer`) |
| `widget_url` / `widgetUrl` / `pageurl` / `websiteURL` | да** | вход в браузерный лейн (виджет) |
| `user_agent` / `userAgent` | нет | UA |
| `seed` | нет | детерминизм трека |

`*` — для серверного API. `**` — если JWT нет, но есть страница: эскалация.

```json
{"type": "hunt", "host": "https://api.integrator.example", "session_jwt": "<jwt>"}
```

## 3. Endpoints / flow

`endpoints.py`: `POST /api/hunt/init`, `GET /api/hunt/image`, `POST /api/hunt/verify` — типовые пути, хост из payload.

1. Нет JWT: есть виджет/страница → `ERR_NEEDS_BROWSER`; иначе `ERR_UNSOLVABLE`.
2. Нет `host` → `ERR_UNSOLVABLE` (`missing payload fields: ['host']`).
3. `POST {host}/api/hunt/init` с `Authorization: Bearer <jwt>` → `wire.parse_challenge` (вариант, картинка, промпт).
4. Картинка: `data:` локально, иначе `GET`.
5. CV: слайдер (`bg`+`piece`) → `SLIDER`; иначе `CLICK` с промптом цели.
6. `POST verify` → `wire.find_token`; пусто → `ERR_CHALLENGE_EXPIRED`; ответ оборачивается в `data`.

Браузерный лейн (`browser.py`): открывает `widget_url`, poll-лит токен (POLL_JS, по умолчанию 90 с).

## 4. Solution keys

`SolutionKind.DATA_NEST` → `data: {"token": …}` (`domain/solution.py` для `HUNT`). Плоский `token` в ответе не появляется намеренно.

```json
{"data": {"token": "<hunt token>"}}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_NEEDS_BROWSER` | `hunt widget mode needs the browser lane` | нет JWT, есть страница | эскалация |
| `ERR_UNSOLVABLE` | `needs host + session_jwt, or widget_url`, `missing payload fields: ['host']` | пустой payload | требовать JWT+host или widget_url |
| `ERR_UNSOLVABLE` | `slider challenge has no piece image`, `click challenge has no target prompt`, `image body is empty` | смена формы | `keys`/`variant` в контексте |
| `ERR_CHALLENGE_EXPIRED` | `verify returned no token` | раунд потреблён | новый раунд |
| `ERR_CONFIG` | `hunt solver needs ctx.http` | контекст без HTTP-клиента (не штатный воркер) | собрать контекст как в `worker_http` |
| `ERR_GENERIC` | `hunt init failed`/`not JSON` | 5xx/не-JSON | `status` в контексте |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_hunt.py` (JWT-путь, слайдер/клик, эскалация, ошибки).
- Живого интегратора нет; `endpoints.py` прямо говорит «hosts vary per integration». Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "hunt-live", "clientKey": "<canary-key>",
 "task": {"type": "hunt", "host": "https://<api-host>", "session_jwt": "<jwt>"},
 "expect": {"status": "ready", "solution_contains": ["data"]}}
```

## 7. FILL / live-риски

1. Пути `/api/hunt/*` — типовые, не подтверждённые живым интегратором: другой интегратор = другие пути, и солвер их не угадает.
2. `X-HD`-фингерпринт виджета HTTP-путь собрать не может — отсюда эскалация; браузерный лейн требует свежего `browser`-heartbeat.
3. Селектор виджета в `browser.py` помечен `FILL`: пиннить после захвата на целевом сайте.
4. JWT живёт ограниченно; просроченный даёт `ERR_CHALLENGE_EXPIRED`, а не `ERR_UNSOLVABLE`.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_hunt.py
uv run ruff check src/captcha/solvers/interactive/hunt
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/hunt/solver.py), [wire.py](../src/captcha/solvers/interactive/hunt/wire.py), [endpoints.py](../src/captcha/solvers/interactive/hunt/endpoints.py), [browser.py](../src/captcha/solvers/interactive/hunt/browser.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
