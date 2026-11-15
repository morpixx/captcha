# Runbook: tencent

## 1. Snapshot

- **captcha type:** `CaptchaType.TENCENT` — Tencent TCaptcha (防水墙): slider или 3-point click, ответ = `ticket` + `randstr`.
- **antibot family:** Tencent Cloud captcha (TCaptcha); `AntibotEngine` для него нет — это вендорский тип, а не движок.
- **lane:** `cv` (`domain/catalog.py`), **mode:** `both` (`solve_http` + `solve_browser`).
- **tier:** 3 — `experimental=True`: протокол собран по публичному SDK, живых сессий нет.
- **code:** `solvers/interactive/tencent/{solver,wire,endpoints,browser}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | страница с виджетом; уходит в `entry_url` и в referer |
| `aid` | да | app-id виджета; `_require(payload, "aid")` — без него `ERR_UNSOLVABLE` |
| `lang` | нет | язык раунда, дефолт `en` (иначе `task.language`) |
| `showtype` | нет | `popup` по умолчанию |
| `uid` | нет | идентификатор клиента виджета |
| `ua_b64` | нет | UA в base64, как его шлёт SDK |
| `drag_y` | нет | строка пазла для `ans` слайдера (дефолт 0) |
| `cookies` | нет | только браузерный лейн |

```json
{"type": "tencent", "websiteURL": "https://target.example/login", "aid": "<aid>"}
```

Официального имени задачи 2captcha у типа нет (`docs/protocol.md`, «Имена типов»), маппинга
`appId` → `aid` в gateway тоже нет — клиент обязан прислать `aid` как есть.

## 3. Endpoints / flow

Хосты и пути — `solvers/interactive/tencent/endpoints.py`:

1. `GET https://t.captcha.qq.com/cap_union_prehandle` — `aid, accver=1, showtype, ua, noheader=1,
   fb=1, aged=0, enableAged=0, enableDarkMode=0, grayscale=1, clientype=2, cap_cd=, uid, lang,
   entry_url, sess=` → `sess` + тип раунда.
2. `GET …/cap_union_new_show` — только если prehandle не принёс `cdnPic1`/`bg`: картинки,
   `pow_prefix`, `pow_difficulty` и echo-поля (`subsid, vsig, websig, eks, tlg, spt, fpinfo,
   cdata, rnd, uid, lang`).
3. `GET <cdnPic1>` (+ `<cdnPic2>`) — фон и пазл.
4. CV in-process через `InteractiveBase._solve_cv`: `SLIDER` при наличии пазла, иначе `CLICK`.
5. PoW локально: `sha256(pow_prefix + counter)` с числом ведущих hex-нулей из
   `pow_difficulty` (`"10000"` → 4). Чистый Python, `MAX_POW_ITERATIONS = 2_000_000`.
6. `collect` — TDC-отпечаток (`window.TDC.getData(true)`) из `ctx.extra["tencent_tdc"]`;
   без продюсера солвер бросает `NeedsBrowserError`, а не отправляет пустую строку.
7. `POST …/cap_union_new_verify` (form) — `aid, sess, ans, collect, showtype, accver=1,
   protocol=https`, `pow_answer`/`pow_calc_time`, echo-поля → `{"ret":0,"ticket":"t03…","randstr":"@…"}`.

`ans`: слайдер `"<x>,<y>"`, клик `"x1,y1|x2,y2|x3,y3"` (`wire.format_ans`).

Браузерный лейн (`tencent/browser.py`) не двигает слайдер и не подделывает `collect`: он
открывает страницу, опрашивает поле `ticket`/`randstr` (или объект на `window` с `ticket`) и
возвращает то, что оставил виджет. Виджет, который требует интерактива, не даёт ticket — это
`ChallengeExpiredError` с причиной `challenge_shown`, а не «успех».

## 4. Solution keys

`SolutionKind.DATA_NEST` (`domain/solution.py`): `data: {randstr, ticket}` + `headers:
{User-Agent}`, если UA известен. `extra` проходит насквозь, поэтому в ответе также видны
`ticket`, `randstr`, `cv_kind`. В HTTP-пути UA в `extra` не кладётся, значит `headers` не
появится — клиент подставляет свой.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `aid`; prehandle без `sess`; show без картинки; CV не дал точек | битый payload или виджет сменил ответ | платить не за что, вернуть клиенту как есть |
| `ERR_NEEDS_BROWSER` | задача ушла в браузерный лейн | нет продюсера TDC (`ctx.extra["tencent_tdc"]`) | включить продюсера или решать в браузере |
| `ERR_CHALLENGE_EXPIRED` | `errorCode` 9/12, или текст про expire/timeout/invalid sess | раунд протух между show и verify | ускорить шаги 3–7, повторить |
| `ERR_SENSOR` | `errorCode` 7, или текст про collect/tdc/fingerprint | TDC-отпечаток не принят | свежий профиль, другой UA/IP |
| `ERR_CONFIG` | «requires ctx.http» / «browser lane needs ctx.browser» | лейн без клиента | см. §7 |
| `ERR_SENSOR` | PoW не сошёлся за бюджет итераций | `pow_difficulty` выше 2M попыток | поднять `MAX_POW_ITERATIONS`, замерить |
| `ERR_INTERNAL` (`CaptchaError`) | ticket есть, но `errorCode` != 0; или ticket нет | vendor-ответ не по схеме | логи `keys`/`errorCode`, обновить `wire.parse_verify` |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_tencent.py` (фейки в `tests/solvers/interactive/vendor_fakes.py`).
- Отдельного каталога фикстур у типа нет (в отличие от `tests/solvers/interactive/fixtures/geetest/`).
- Кейс в канарейку (`runbooks/canary.md` §2), **ещё не добавлен**:

```json
{"name": "tencent-live", "clientKey": "<canary-key>",
 "task": {"type": "tencent", "websiteURL": "https://<host>/login", "aid": "<aid>"},
 "expect": {"status": "ready", "solution_contains": ["data", "headers"]}}
```

## 7. FILL / live-риски

1. **Продюсер TDC не подключён нигде в `services/`** (grep `tencent_tdc` — только сам солвер).
   Значит HTTP-путь в живом воркере всегда доходит до шага 6 и эскалирует.
2. **cv-лейн без транспорта.** `services/worker_cv/worker.py` строит `SolverContext` без
   `http` (строка 140), а тип живёт в лейне `cv`: по коду `solve_http` падает `ERR_CONFIG` до
   шага 1. Плюс `ERR_NEEDS_BROWSER` в этом воркере не обрабатывается отдельно — задача уходит
   в общий retry/DLQ, в браузерный лейн не перекладывается. На живом воркере не проверено.
3. **Не проверено live:** `errorCode`-таблица (9/12 expired, 7 sensor), форма `ans` для
   клика, набор echo-полей, поведение `pow_difficulty` на реальном aid.
4. Браузерный лейн читает только готовый ticket: если сайт показывает слайдер, ответа не будет.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_tencent.py
uv run ruff check src/captcha/solvers/interactive/tencent
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет, тип не продаётся (`experimental`).`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/tencent/solver.py),
  [wire.py](../src/captcha/solvers/interactive/tencent/wire.py),
  [endpoints.py](../src/captcha/solvers/interactive/tencent/endpoints.py),
  [browser.py](../src/captcha/solvers/interactive/tencent/browser.py)
- протокол: [docs/protocol.md](../docs/protocol.md) (форма решения, коды ошибок)
- шаблон кейса канарейки: [runbooks/canary.md](canary.md)
