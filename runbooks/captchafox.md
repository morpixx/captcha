# Runbook: captchafox

## 1. Snapshot

- **captcha type:** `CaptchaType.CAPTCHAFOX` (`captchafox`) — CaptchaFox: слайд или select по картинке, ответ — токен верификатора (`cf-captcha-response` в браузере).
- **antibot family:** нет. Свой API `api.captchafox.com`; тела запросов — gzip-JSON с 2-байтным заголовком и побайтовым XOR (`wire.encode_body`).
- **lane:** `browser` (по каталогу), **mode:** `both`, `browser=True`.
- **tier:** 3 — `experimental=True`; сборка `api.js` 2026-11, живого прогона нет.
- **code:** `solvers/interactive/captchafox/{solver,wire,endpoints,browser}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `sitekey` | да | алиасы `websiteKey`, `key`, `siteKey` |
| `websiteURL` | да | страница; из неё хост в телах запросов |
| `proof` | да* | `{"cs": …, "k": …}` — proof виджета; допустима и плоская форма `cs`/`k` |
| `target` | нет | текст цели для select-режима |
| `trail` | нет | список точек трека клиента |
| `lang` / `language` | нет | по умолчанию `en` |

`*` — без proof HTTP-путь сразу эскалирует: verify без него всё равно отвергнут.

```json
{"type": "captchafox", "sitekey": "<sitekey>", "websiteURL": "https://target.example/",
 "proof": {"cs": "…", "k": "…"}}
```

## 3. Endpoints / flow

`endpoints.py`: `GET /captcha/<sitekey>/config`, `POST /captcha/<sitekey>/challenge`, `POST /captcha/verify` на `api.captchafox.com`; заголовки `X-Pulse = 66bfc668cc8dc345`, `X-Ember = 1db87116` (сборка 2026-11).

1. `GET config` — параметры челленджа.
2. Нет proof → `ERR_NEEDS_BROWSER` (в контексте — `config_keys` для диагностики).
3. `POST challenge` (тело `wire.encode_body`, `Content-Type: text/plain`) → `kind` (`slide`/`select`/`attest`/`audio`) и картинки. Если ответ сразу несёт токен — задача решена.
4. `_answer`: `attest` → пустой ответ; `slide` → CV `SLIDER` (`bg`+`player`); иначе CV `CLICK` с промптом.
5. `POST verify` — `wire.verify_body` (`cs`, опционально `k.v=2`, хост, язык, трек, elapsed) → `wire.parse_verified`.
6. Браузерный лейн — `browser.py`, ждёт `cf-captcha-response`.

Ассеты: `data:`-URI декодируются локально, иначе `GET` по URL; пустое тело → `ERR_CHALLENGE_EXPIRED`.

## 4. Solution keys

`SolutionKind.TOKEN` → `token` и `userAgent` (если у задачи был UA).

```json
{"token": "<verified token>", "userAgent": "<ua>"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_NEEDS_BROWSER` | `the verify body needs the widget's __cf_wapi proof` | нет `proof`/`cs` | прислать proof или браузерный лейн |
| `ERR_UNSOLVABLE` | `payload missing sitekey/websiteKey`, `payload missing websiteURL`, `asset is neither base64 nor a URL` | пустой/битый payload | требовать ключ, URL и ассеты |
| `ERR_CHALLENGE_EXPIRED` | `empty challenge asset` | CDN отдал пусто | новый раунд |
| `ERR_CONFIG` | `captchafox solver requires ctx.http` | воркер без HTTP | см. §7 |
| `ERR_CHALLENGE_EXPIRED` | 401/403/409/410/428/429 в `wire` | челлендж протух | новый раунд |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_captchafox.py` (config/challenge/verify, эскалация без proof, кодирование тела).
- Живых дампов `api.js` нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "captchafox-live", "clientKey": "<canary-key>",
 "task": {"type": "captchafox", "sitekey": "<sitekey>", "websiteURL": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. `X-Pulse`/`X-Ember` и формат `encode_body` привязаны к сборке 2026-11; в `endpoints.py` это помечено как `FILL: read them from the current cdn.captchafox.com/api.js`. Новый релиз CDN = отказ верификатора.
2. Proof (`cs`, `k`) HTTP-путь получить не может — только клиент или браузерный лейн.
3. Лейн по каталогу `browser`, но солвер умеет HTTP: при живом `browser`-heartbeat и proof в payload HTTP-путь короче и дешевле.
4. `audio`-вид челленджа обрабатывается как `select`/`click` (в `_answer` отдельной ветки нет) — на живом тенанте это может дать неверный ответ.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_captchafox.py
uv run ruff check src/captcha/solvers/interactive/captchafox
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/captchafox/solver.py), [wire.py](../src/captcha/solvers/interactive/captchafox/wire.py), [endpoints.py](../src/captcha/solvers/interactive/captchafox/endpoints.py), [browser.py](../src/captcha/solvers/interactive/captchafox/browser.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
- браузерный лейн: [browser.md](browser.md)
