# Runbook: binance

## 1. Snapshot

- **captcha type:** `CaptchaType.BINANCE` (`binance`) — Binance bCAPTCHA2: слайдер `getCaptcha` → `validateCaptcha`, ответ — токен `captcha#...`.
- **antibot family:** нет. `bapi/composite/v1/public/antibot/*` с набором фингерпринт-заголовков фронтенда.
- **lane:** `cv` (по каталогу, нужен слайдер), **mode:** `http`, `needs_js_runtime=True`.
- **tier:** 3 — `experimental=True`; ключи шифрования ответа помечены `FILL`.
- **code:** `solvers/interactive/binance/{solver,wire,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `bizScene` | да | алиасы `biz_scene`, `bizType` |
| `validateId` / `validate_id` | нет | id сессии, если precheck уже прогоняли |
| `piece_url` / `pieceUrl` | да* | картинка пазла, когда челлендж вернул только фон |
| `data` / `answer_data` | нет | готовый зашифрованный блоб — используется как есть |
| `sign_key` / `signKey` | нет | ключ для HMAC-поля `s` |
| `bnc_uuid`, `csrftoken`, `device_info`, `captcha_sdk_version`, `x_captcha_se`, `fvideo_id`, `trace_id` | нет | фингерпринт-заголовки (алиасы с дефисами поддержаны) |
| `clienttype`, `lang`, `pageurl`, `user_agent` | нет | заголовки запроса |
| `seed` | нет | детерминизм трека |

`*` — обязателен, если `getCaptcha` не вернул пазл.

```json
{"type": "binance", "bizScene": "login", "bnc_uuid": "<uuid>", "csrftoken": "<token>",
 "device_info": "<json>", "piece_url": "https://bin.bnbstatic.com/..."}
```

## 3. Endpoints / flow

`endpoints.py`: `POST https://www.binance.com/bapi/composite/v1/public/antibot/getCaptcha`, `.../validateCaptcha`; precheck `/bapi/accounts/v1/public/account/security/request/precheck`; картинки — `https://bin.bnbstatic.com`.

1. `GETCAPTCHA` c `{bizScene, securityCheckResponseValidateId}` и фингерпринт-заголовками.
2. `wire.raise_for_failure` + `wire.parse_challenge`: `sig`, `salt`, `bg`, `piece`, `captcha_type`, `tag`, `validate_id`, ширина пазла.
3. Картинки: `data:`-URI декодируется локально, иначе `GET`.
4. CV `SLIDER` (`bg`, `piece`, `piece_width`, `seed`); неположительный offset → `ERR_UNSOLVABLE`.
5. `wire.build_track` + `wire.encode_answer` (или готовый `data` из payload; `s` = HMAC, если дан `sign_key`).
6. `POST validateCaptcha` → `wire.token_from`; пусто → `ERR_CHALLENGE_EXPIRED`.

BOX/grid-апгрейд после серии неудач — другой челлендж и здесь не поддержан.

## 4. Solution keys

`SolutionKind.TOKEN` → `token` (= `captcha#...`), `userAgent`. `extra`: `validateId`, `captchaType`, `sessionId` (если пришёл).

```json
{"token": "captcha#...", "validateId": "…", "captchaType": "SLIDER", "userAgent": "<ua>"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `missing payload fields: ['bizScene']` | нет сцены | требовать `bizScene` |
| `ERR_UNSOLVABLE` | `challenge carries one image: pass piece_url` | в ответе только фон | прислать `piece_url` |
| `ERR_UNSOLVABLE` | `slider resolved to a non-positive offset` | CV не нашёл пазл | другое `seed`/кроп |
| `ERR_CHALLENGE_EXPIRED` | `validateCaptcha returned no token` | ответ отвергнут | новый раунд |
| `ERR_CHALLENGE_EXPIRED` | 401/403/409/410/428/429 | подпись/сессия | свежие заголовки |
| `ERR_CONFIG` | `binance solver needs ctx.http` | воркер без HTTP | см. §7 |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_binance.py` (get/validate, готовый `data`, `sign_key`, отсутствие полей, ошибки).
- Живых дампов `getCaptcha` нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "binance-live", "clientKey": "<canary-key>",
 "task": {"type": "binance", "bizScene": "login", "piece_url": "<url>"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. **Лейн против контекста.** Каталог держит тип в `cv`, а `worker_cv` собирает контекст без `http` — на нём задача падает в `ERR_CONFIG` до первого запроса.
2. `wire.py` помечает `FILL` на наборе plaintext-полей ответа, точной MAC-схеме `s` и кодах ошибок: шифрование собрано по публичным разборам, не по живому захвату.
3. Заголовки (`bnc-uuid`, `device-info`, `x-captcha-se`) обязан прислать клиент; без них — 4xx и `ERR_CHALLENGE_EXPIRED`.
4. BOX/grid-челлендж после неудач не поддержан.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_binance.py
uv run ruff check src/captcha/solvers/interactive/binance
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/binance/solver.py), [wire.py](../src/captcha/solvers/interactive/binance/wire.py), [endpoints.py](../src/captcha/solvers/interactive/binance/endpoints.py)
- слайдер: [slider.md](slider.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
