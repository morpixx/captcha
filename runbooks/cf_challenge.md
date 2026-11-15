# Runbook: cf_challenge

## 1. Snapshot

- **captcha type:** `CaptchaType.CF_CHALLENGE` — Cloudflare managed challenge / IUAM, ответ = `cf_clearance` + cookie-jar + UA.
- **antibot family:** `AntibotEngine.CLOUDFLARE` (builder — `engines/cloudflare/builder.py`, пока с `# FILL`).
- **lane:** `http`, **mode:** `both`; `solve_http` — только точка эскалации, работу делает браузер.
- **tier:** 3 — `experimental=True` (`version 0.1.0`).
- **code:** `solvers/antibot/cloudflare/solver.py` + `_common.py`, `_cookies.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` (или `websiteUrl`/`pageurl`/`page_url`/`site_url`) | да | страница под челленджем; ≤2048, только http(s) |
| `cookies` | нет | стартовый jar (строка `a=b; c=d` или список `{name,value,domain?,path?}`) |
| `proxy` | нет | прокси задачи (уходит в provider, а не в HTTP-клиент) |

```json
{"type": "cf_challenge", "websiteURL": "https://target.example/"}
```

## 3. Endpoints / flow

1. HTTP-путь: `clean_url(website_url(task))`; если `ctx.browser` есть — сразу браузерный путь;
   иначе `NeedsBrowserError` (`cf_clearance` выдаётся сервером, переносить в HTTP-клиент нечего).
2. Браузерный путь: одна свежая сессия, навигация.
3. Ожидание: опрос каждые `POLL_S = 0.5` (≤ 1/6 бюджета) — нужен **одновременно** cookie
   `cf_clearance`, отсутствие маркеров челленджа (`#challenge-stage`, `#challenge-running`,
   `#challenge-form`, `#cf-please-wait`, title/body-маркеры) и URL вне
   `/cdn-cgi/challenge-platform/` / `/cdn-cgi/l/chk_`.
4. Через `NUDGE_AFTER_S = 8` с — `Tab`+`Space` (CDP-клик по managed-challenge Turnstile
   отклоняется, см. `_common.nudge_keys`).
5. Просрочка без всех трёх условий — `ChallengeExpiredError` с причиной
   `clearance_before_redirect` или `no_clearance`; в контекст попадает только число cookie,
   не значения (контракт §5).

## 4. Solution keys

`SolutionKind.CLOUDFLARE`: `cf_clearance`, `token` (= то же значение), `cookies` (весь jar),
`userAgent` — UA страницы, которая держит cookie. Клиент, подставивший свой UA, получит
невалидный clearance.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_NEEDS_BROWSER` | HTTP-лейн эскалирует | нет провайдера в этом воркере | штатный путь |
| `ERR_CHALLENGE_EXPIRED` | «no usable cf_clearance (no_clearance)» | страница не прошла челлендж | новый профиль/IP, больше бюджета |
| `ERR_CHALLENGE_EXPIRED` | «clearance_before_redirect» | cookie появился, но интерстишл ещё висит | Cloudflare не примет такую пару — ждать редиректа |
| `ERR_CHALLENGE_EXPIRED` | «page script returned no state» | страница не та / скрипт убит | проверить URL и прокси |
| `ERR_CONFIG` | «the page reported no user-agent» | clearance UA-bound | не подменять UA провайдера |
| `ERR_CONFIG` | «SolverContext.browser is None» | лейн без провайдера | проверить деплой |

## 6. Canary / фикстуры

- Тест: `tests/solvers/antibot/test_cloudflare_challenge.py`.
- Фикстур-дампов челленджа нет (страница генерируется под запрос).
- Кейс (не добавлен; нужен сайт, стабильно показывающий managed challenge):

```json
{"name": "cf-challenge", "clientKey": "<canary-key>",
 "task": {"type": "cf_challenge", "websiteURL": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["cf_clearance", "cookies", "userAgent"]}}
```

## 7. FILL / live-риски

1. Тип полностью зависит от браузерного лейна: без `worker_browser` продавать его нельзя
   (`docs/architecture.md`, «worker-browser и canary не в compose»).
2. `engines/cloudflare/builder.py` содержит `# FILL` (shim-поверхность, порт fingerprint) — этот
   солвер билдер не использует, но «HTTP-решение managed challenge» остаётся нереализованным.
3. Маркеры челленджа — эвристика по DOM/title; при смене разметки ожидание сломается молча в
   сторону таймаута.
4. Pass rate не измерен; бюджет 90 с на страницу может не совпасть с реальным временем CF.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_cloudflare_challenge.py
uv run ruff check src/captcha/solvers/antibot/cloudflare
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/cloudflare/solver.py),
  [_common.py](../src/captcha/solvers/antibot/cloudflare/_common.py),
  [_cookies.py](../src/captcha/solvers/antibot/cloudflare/_cookies.py)
- родственный runbook: [turnstile.md](turnstile.md)
- лейн браузера: [runbooks/browser.md](browser.md)
- разбор: `docs/reverse-2026.md` §4
