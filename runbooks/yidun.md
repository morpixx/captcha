# Runbook: yidun

## 1. Snapshot

- **captcha type:** `CaptchaType.YIDUN` — NetEase Yidun (易盾): slider или 3-point click, ответ = `validate`.
- **antibot family:** NetEase Yidun; отдельного `AntibotEngine` нет.
- **lane:** `browser` (по каталогу), **mode:** `both` (`solve_http` + `solve_browser`).
- **tier:** 3 — `experimental=True`, протокол по публичному SDK, live не снят.
- **code:** `solvers/interactive/yidun/{solver,wire,endpoints,browser}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | страница виджета; уходит в `referer` запроса `get` |
| `captcha_id` | да | id капчи (в SDK — `id`); без него `ERR_UNSOLVABLE` |
| `fp` | нет | device-hint SDK; пустая строка допустима |
| `ctype` | нет | `2` слайдер (дефолт), `3` клик (`ep.TYPE_SLIDER`/`TYPE_CLICK`) |
| `version` | нет | версия SDK в запросе, дефолт `2.25.2` |
| `lang` | нет | язык, иначе `task.language`, иначе `en` |
| `cookies` | нет | только браузерный лейн |

```json
{"type": "yidun", "websiteURL": "https://target.example/login", "captcha_id": "<id>", "ctype": 2}
```

## 3. Endpoints / flow

`solvers/interactive/yidun/endpoints.py`: API-хост `c.dun.163.com`, SDK — `cstaticdun.126.net`.

1. `GET https://c.dun.163.com/api/v2/get` — `id, fp, https=1, type, version, dpr=1, dps=0, lang,
   dft=0, referer` → `{token, bg, front, zoneId, type}` (JSON или JSONP `__wmjsonp(...)`).
2. `GET <bg>` (+ `<front>`) — картинки; наличие `front` определяет `SLIDER` vs `CLICK`.
3. CV in-process (`InteractiveBase._solve_cv`).
4. Плейнтекст ответа: слайдер `{x, t, s}`, клик `{p: [[x,y]…], t}` (`wire.answer_plaintext`).
5. Запечатывание в `data` + `cb` — только через `ctx.extra["yidun_signer"]`
   (`call({plain, token, type})` → `{"data","cb"}`); без него `NeedsBrowserError`.
   Шифр SDK не переimplementируется намеренно.
6. `POST https://c.dun.163.com/api/v2/check` — `id, token, data, cb, width, extraData="",
   runEnv=10, type` → `validate`.

`zoneId` солвер только читает: финальную подпись `validate` под `zoneId` делает backend сайта,
это не наша часть протокола.

Браузерный лейн (`yidun/browser.py`) ищет `validate` в полях `*validate*`/`necaptcha*` или
на `window` (минимум 16 символов) и не двигает капчу. Пусто → `ChallengeExpiredError`
(`challenge_shown` / `no_validate`).

## 4. Solution keys

`SolutionKind.TOKEN`: `token` = `validate`, плюс `userAgent`. `extra` проходит насквозь, поэтому
в ответе видно и `validate`, и `cv_kind` (в браузерном лейне — `path: "browser"`).

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `captcha_id`; `get` без `token`/bg; силер вернул не объект; пустые точки клика | битый payload или смена схемы | сверить `keys` в контексте ошибки |
| `ERR_NEEDS_BROWSER` | эскалация в браузер | нет `ctx.extra["yidun_signer"]` | подключить силер или решать в браузере |
| `ERR_CHALLENGE_EXPIRED` | `code=2306`, или текст expire/timeout/overdue/过期/超时 | раунд протух | ускорить шаги 1–6 |
| `ERR_CONFIG` | `yidun browser lane needs ctx.browser` | контекст без browser-провайдера (не штатный воркер) | штатный `worker_browser` его даёт |
| `ERR_INTERNAL` (`CaptchaError`) | `validate` при `result:false`; или `validate` нет | vendor-ответ не по схеме | логи `result`/`error`/`msg` |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_yidun.py`.
- Каталога фикстур у типа нет.
- Кейс (не добавлен):

```json
{"name": "yidun-live", "clientKey": "<canary-key>",
 "task": {"type": "yidun", "websiteURL": "https://<host>/login", "captcha_id": "<id>"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. `yidun_signer` не подключён в `services/` — HTTP-путь всегда упирается в шаг 5.
2. Лейн `browser`: `worker_browser` даёт `ctx.browser`, а `ERR_NEEDS_BROWSER` из HTTP-пути
   обрабатывает `worker_http` (один прыжок, `MAX_LANE_HOPS`). В cv-лейн тип не маршрутизируется
   (`domain/catalog.py`). По коду, live не проверено.
3. Не проверено live: значения `type`/`runEnv`, набор echo-полей, реальные `width` и формат
   `cb` (шифр не снят — это осознанный отказ, а не FILL).
4. `fp` пустой по умолчанию: похоже на оценку риска вендором, влияние не измерено.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_yidun.py
uv run ruff check src/captcha/solvers/interactive/yidun
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/yidun/solver.py),
  [wire.py](../src/captcha/solvers/interactive/yidun/wire.py),
  [endpoints.py](../src/captcha/solvers/interactive/yidun/endpoints.py),
  [browser.py](../src/captcha/solvers/interactive/yidun/browser.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
- канарейка: [runbooks/canary.md](canary.md)
