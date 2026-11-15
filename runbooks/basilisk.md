# Runbook: basilisk

## 1. Snapshot

- **captcha type:** `CaptchaType.BASILISK` (`basilisk`) — Basilisk: ad-funded click-капча «кликни логотип компании»; ответ — 32-hex `captcha_response`.
- **antibot family:** нет. API `api.basiliskcaptcha.com`, виджет с `cdn.basiliskcaptcha.com`.
- **lane:** `cv` (по каталогу, нужен click-детектор; `worker_cv` даёт per-task `ctx.http`), **mode:** `http`.
- **tier:** 3 — `experimental=True`; имена полей ответа помечены `FILL`.
- **code:** `solvers/interactive/basilisk/{solver,wire,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `sitekey` | да | ключ виджета; без него `ERR_UNSOLVABLE` |
| `origin` / `pageurl` / `websiteURL` | нет | страница: Referer и поле `origin` в теле `challenge` |
| `user_agent` / `userAgent` | нет | UA; попадает в `headers` ответа |
| `seed` | нет | детерминизм трека |

```json
{"type": "basilisk", "sitekey": "<sitekey>", "origin": "https://target.example/"}
```

## 3. Endpoints / flow

`endpoints.py`: `POST https://api.basiliskcaptcha.com/v1/challenge`, `POST https://api.basiliskcaptcha.com/v1/verify`.

1. `POST /v1/challenge` (`{siteKey, origin}`) → `wire.parse_challenge`: `challengeId`, `image`, `target`.
2. Картинка: `data:` локально, иначе `GET`.
3. CV `CLICK` с `{image, target, seed}`.
4. `POST /v1/verify` c `{challengeId, coords, track, dwell, siteKey}` (`wire.answer_body`) → `captcha_response`.
5. Пусто → `ERR_CHALLENGE_EXPIRED`: ответ привязан к челленджу и сессии, повтор с тем же id бессмысленен.

## 4. Solution keys

`SolutionKind.DATA_NEST` → `data: {"captcha_response": <32 hex>}` и `headers` с UA (`domain/solution.py` для `BASILISK`).

```json
{"data": {"captcha_response": "3f2a…"}, "headers": {"User-Agent": "<ua>"}}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `missing payload fields: ['sitekey']` | нет ключа | требовать `sitekey` |
| `ERR_UNSOLVABLE` | `image body is empty`, `broken data URI` | битый ассет | новый раунд |
| `ERR_CHALLENGE_EXPIRED` | `verify returned no captcha_response` | челлендж потреблён/отвергнут | новый раунд |
| `ERR_CHALLENGE_EXPIRED` | 401/403/409/410/428/429 в `wire` | сессия/токен | свежий раунд |
| `ERR_CONFIG` | `basilisk solver needs ctx.http` | контекст без HTTP-клиента (не штатный воркер) | собрать контекст как в `worker_cv` |
| `ERR_GENERIC` | `basilisk challenge failed`/`not JSON` | 5xx/не-JSON | `status` в контексте |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_basilisk.py` (challenge/verify, отсутствие sitekey, ошибки).
- Живых дампов нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "basilisk-live", "clientKey": "<canary-key>",
 "task": {"type": "basilisk", "sitekey": "<sitekey>", "origin": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["data"]}}
```

## 7. FILL / live-риски

1. **Транспорт.** `worker_cv` выдаёт per-task `ctx.http` (`_run_one` → `context.task_context`); `ERR_CONFIG` из §5 возможен только на контексте, собранном без клиента.
2. `wire.py` помечает `FILL` на именах полей трека (`coords`/`track`): отправляются оба варианта, живой захват не подтверждает ни один.
3. `dwell` — расчётная величина из трека; требует ли сервер «человеческую» паузу, не проверено.
4. Ответ одноразовый и привязан к сессии/IP: клиент обязан отправить его сразу.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_basilisk.py
uv run ruff check src/captcha/solvers/interactive/basilisk
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/basilisk/solver.py), [wire.py](../src/captcha/solvers/interactive/basilisk/wire.py), [endpoints.py](../src/captcha/solvers/interactive/basilisk/endpoints.py)
- клик-делегат: [click.md](click.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
