# Runbook: atbcaptcha

## 1. Snapshot

- **captcha type:** `CaptchaType.ATBCAPTCHA` — atbCaptcha / AiSecurius (顶象): slider или 3-point click, ответ = `captchaVerification`.
- **antibot family:** AiSecurius (atb); отдельного `AntibotEngine` нет.
- **lane:** `cv`, **mode:** `both`.
- **tier:** 3 — `experimental=True`; схема по публичному SDK, live не снят.
- **code:** `solvers/interactive/atbcaptcha/{solver,wire,endpoints,browser}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | страница виджета (браузерный лейн), иначе `pageurl` |
| `captcha_id` | да | id капчи; без него `ERR_UNSOLVABLE` |
| `ctype` | нет | `1` слайдер (дефолт), `2` клик (`ep.TYPE_SLIDER`/`TYPE_CLICK`) |
| `lang` | нет | язык, иначе `task.language`, иначе `en` |
| `cookies` | нет | только браузерный лейн |

```json
{"type": "atbcaptcha", "websiteURL": "https://target.example/", "captcha_id": "<id>"}
```

## 3. Endpoints / flow

`solvers/interactive/atbcaptcha/endpoints.py`: `cap.aisecurius.com`, пути `/cap-api/v1/get` и
`/cap-api/v1/check`.

1. `GET /cap-api/v1/get?captchaId=&lang=&type=&ts=` → `data: {token, pic, blockPic}`.
2. `GET <pic>` (+ `<blockPic>`) — фон и пазл; наличие пазла → `SLIDER`, иначе `CLICK`.
3. CV in-process (`InteractiveBase._solve_cv`).
4. Плейнтекст: слайдер `{x, t}`, клик `{pts: [[x,y]…], t}` (`wire.answer_plaintext`).
5. `data` — AES-GCM-конверт SDK; берётся только из `ctx.extra["atb_sealer"]`
   (`seal({plain, token, type})` → строка). Без силера — `NeedsBrowserError`.
6. `POST /cap-api/v1/check` (JSON) — `{captchaId, token, data, type, ts}` →
   `data.captchaVerification` при `success: true`.

Браузерный лейн читает `captchaVerification` из полей с `captchaverification`/`captcha_verification`
или с `window` (≥16 символов), капчу не двигает. Пусто → `ChallengeExpiredError`.

## 4. Solution keys

`SolutionKind.TOKEN`: `token` = `captchaVerification`, плюс `userAgent`. `extra` проходит
насквозь, поэтому в ответе также `captcha_id` и `cv_kind`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `captcha_id`; `get` без token/pic; силер вернул пусто; клик без точек | битый payload / смена схемы | сверить `keys`, `code`, `msg` в контексте |
| `ERR_NEEDS_BROWSER` | эскалация | нет `ctx.extra["atb_sealer"]` | подключить силер или браузерный лейн |
| `ERR_CHALLENGE_EXPIRED` | `code`/текст expire/timeout/overdue/过期/超时 | раунд протух | ускорить шаги 1–6 |
| `ERR_CONFIG` | requires ctx.http / needs ctx.browser | лейн без клиента | см. §7 |
| `ERR_INTERNAL` (`CaptchaError`) | verification при `success != true`; или verification нет | vendor-ответ не по схеме | логи `code`/`msg` |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_atbcaptcha.py`; фикстур типа нет.
- Кейс (не добавлен):

```json
{"name": "atbcaptcha-live", "clientKey": "<canary-key>",
 "task": {"type": "atbcaptcha", "websiteURL": "https://<host>/", "captcha_id": "<id>"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-risks

1. `atb_sealer` не подключён в `services/` — HTTP-путь всегда доходит до шага 5.
2. Лейн `cv`: `services/worker_cv/worker.py` создаёт `SolverContext` без `http` (строка 140), а
   `ERR_NEEDS_BROWSER` не обрабатывается — эскалации в браузер нет. По коду, live не проверено.
3. Не проверено live: формат `ts`, реальная схема `data` (AES-GCM ключ выводится страницей —
   не воспроизводится), набор полей `get`.
4. `TYPE_CLICK = 2` и `TYPE_SLIDER = 1` взяты из публичного SDK, не сверены с живым тенантом.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_atbcaptcha.py
uv run ruff check src/captcha/solvers/interactive/atbcaptcha
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/atbcaptcha/solver.py),
  [wire.py](../src/captcha/solvers/interactive/atbcaptcha/wire.py),
  [endpoints.py](../src/captcha/solvers/interactive/atbcaptcha/endpoints.py),
  [browser.py](../src/captcha/solvers/interactive/atbcaptcha/browser.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
- канарейка: [runbooks/canary.md](canary.md)
