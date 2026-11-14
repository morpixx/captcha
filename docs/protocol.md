# Протокол

Совместимость с JSON API 2captcha (её же копируют capsolver и capmonster). Клиент меняет
только base URL и `clientKey`, интеграцию не переписывает.

## Эндпоинты

```
POST /createTask     {clientKey, task: {type, websiteURL, websiteKey, ...}} -> {errorId: 0, taskId}
POST /getTaskResult  {clientKey, taskId} -> {status: processing|ready|failed, solution: {...}, cost, endTime}
POST /getBalance     {clientKey} -> {errorId: 0, balance}
GET  /healthz, /readyz, /metrics
GET  /admin/stats, /admin/solvers
```

## Конвенции

1. **HTTP всегда 200.** Бизнес-ошибка приходит как `errorId != 0` + `errorCode` +
   `errorDescription`. Клиентские SDK смотрят только на `errorId`. Реализация:
   `services/gateway/errors.py`.
2. **Payload mode.** Клиент сам собирает sitekey, cookies, свежие sensor-данные. URL-mode
   (мы сами ходим на страницу) — отдельный тариф, не в v0.1.
3. **Один эндпоинт на все типы.** `task.type` решает, кто обработает задачу; остальные
   параметры уходят в `payload` как есть.

## Коды ошибок

| внутренний код | errorId | errorCode |
|---|---|---|
| `ERR_UNSOLVABLE` | 12 | `ERROR_CAPTCHA_UNSOLVABLE` |
| `ERR_TIMEOUT` | 14 | `ERROR_RECAPTCHA_TIMEOUT` |
| `ERR_BALANCE` | 10 | `ERROR_ZERO_BALANCE` |
| `ERR_PROXY` | 23 | `ERROR_PROXY_CONNECT_REFUSED` |
| `ERR_SENSOR` | 24 | `ERROR_SENSOR_INVALID` |
| `ERR_JS_RUNTIME` | 25 | `ERROR_JS_RUNTIME` |
| `ERR_CHALLENGE_EXPIRED` | 26 | `ERROR_CHALLENGE_EXPIRED` |
| `ERR_MODEL_INFERENCE` | 27 | `ERROR_MODEL_INFERENCE` |
| `ERR_TASK_NOT_FOUND` | 16 | `ERROR_TASK_NOT_FOUND` |
| `ERR_UNSUPPORTED_TYPE` | 12 | `ERROR_TASK_NOT_SUPPORTED` |
| `ERR_CONFIG` | 999 | `ERROR_INTERNAL` |

## Форма решения

`getTaskResult.solution` собирается по типу капчи в `domain/solution.py`: клиент ищет
конкретный ключ, и универсальное `token` для половины типов не подходит.

| семейство | ключи |
|---|---|
| reCAPTCHA v2/v3 (+enterprise, invisible, callback) | `gRecaptchaResponse`, `userAgent` |
| hCaptcha (+enterprise) | `gRecaptchaResponse`, `respKey`, `userAgent` |
| Turnstile, FunCaptcha, MTCaptcha, Prosopo, Yidun, Binance, VK, Yandex, CaptchaFox, Cutcaptcha, KeyCaptcha, Capy, Lemin, atb, Twitch, BotGuard, BotDeflector, CyberSiARA | `token`, `userAgent` |
| GeeTest v3 | `challenge`, `validate`, `seccode` |
| GeeTest v4 | `captcha_id`, `lot_number`, `pass_token`, `gen_time`, `captcha_output` |
| Cloudflare Challenge | `cf_clearance`, `cookies`, `token`, `userAgent` |
| DataDome, Akamai, Kasada, PerimeterX, TSPD, Temu | `cookies` + `domains: {host: {cookies}}`, `userAgent` |
| Imperva | то же + `utmvc` |
| Friendly, Altcha, Hunt, Castle, Basilisk, Alibaba, Tencent | вложенный `data` (+ `headers` с UA) |
| AWS WAF | `captcha_voucher` + `existing_token`, либо `cookies["aws-waf-token"]` |
| text / math / image-to-text / audio | `text` |

Абстрактная геометрия — наши собственные ключи, единого 2captcha-стандарта нет:
`objects` (grid), `coordinates` (click/draw/drag/Shein), `bbox`, `angle`, `offset`.

`SolverResult.extra` всегда побеждает: солвер, который знает свой движок, кладёт
vendor-exact поля, и они проходят насквозь.
