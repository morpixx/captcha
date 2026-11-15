# Протокол

Совместимость с JSON API 2captcha (её же копируют capsolver и capmonster) плюс её
form-encoded предшественник. Клиент меняет только base URL и `clientKey`, интеграцию не
переписывает.

## Эндпоинты

```
POST /createTask         {clientKey, task: {type, websiteURL, websiteKey, ...}} -> {errorId: 0, taskId}
POST /getTaskResult      {clientKey, taskId} -> {status: processing|ready|failed, solution: {...}, cost, endTime}
GET  /getTaskResult      то же, поля в query
POST /getBalance         {clientKey} -> {errorId: 0, balance}
GET  /getBalance         то же, поля в query
POST /reportCorrect      {clientKey, taskId} -> {errorId: 0, status: "OK"}
POST /reportIncorrect    {clientKey, taskId} -> {errorId: 0, status: "OK"}

POST /in.php             form: key, method, ... -> OK|<taskId>      (legacy)
POST /res.php            form: key, action, id -> OK|<answer>       (legacy)

GET  /account            {balance, accountId, keyTail, createdAt}   (кабинет)
GET  /account/transactions?limit&offset -> {items: [...]}           (леджер)
POST /account/topup      {amount, provider?, returnUrl?} -> intent
POST /account/topup/{id}/confirm  (X-Admin-Token) -> {credited, balance}
GET  /account/stats?days=7 -> {totals, by_type, daily}

GET  /healthz, /readyz, /metrics
GET  /admin/stats, /admin/solvers          (только при заданном ADMIN_TOKEN)
```

`/admin/*` отвечает 404, если `ADMIN_TOKEN` пуст, и 401 на неверный
`X-Admin-Token`. По умолчанию поверхность выключена.

## Конвенции

1. **HTTP всегда 200.** Бизнес-ошибка приходит как `errorId != 0` + `errorCode` +
   `errorDescription`. Клиентские SDK смотрят только на `errorId`. Реализация:
   `services/gateway/errors.py`.
2. **Payload mode.** Клиент сам собирает sitekey, cookies, свежие sensor-данные. URL-mode
   (мы сами ходим на страницу) не поддерживается.
3. **Один эндпоинт на все типы.** `task.type` решает, кто обработает задачу; остальные
   параметры уходят в `payload` как есть.
4. **Лейн выбирает тип, не клиент.** `http` / `cv` / `protocol` заданы в
   `domain/catalog.py`.

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
| неверный ключ | 1 | `ERROR_KEY_DOES_NOT_EXIST` |
| битый запрос | 21 | `ERROR_BAD_REQUEST` |
| нет слотов | 2 | `ERROR_NO_SLOT_AVAILABLE` |

Legacy добавляет два своих: `ERROR_NO_SUCH_METHOD` (неизвестный `method`) и
`ERROR_BAD_PARAMETERS` (не хватает поля).

## Legacy `/in.php`

Тело — form-encoded или query string. Обязательные: `key`, `method`. Ответ — текст
`OK|<taskId>`, либо код ошибки как текст. С `json=1` — `{"status":1,"request":"<taskId>"}`
или `{"status":0,"request":"<ERROR_CODE>"}`.

| `method` | тип | ключевые поля |
|---|---|---|
| `userrecaptcha` | `recaptcha_v2` (+ `invisible`/`enterprise`) | `googlekey`, `pageurl` |
| `hcaptcha` | `hcaptcha` | `sitekey`, `pageurl`, `host` |
| `turnstile` | `turnstile` | `sitekey`, `pageurl`, `action`, `cdata` |
| `funcaptcha` | `funcaptcha` | `publickey`, `pageurl`, `surl` |
| `geetest` | `geetest_v3` | `gt`, `challenge`, `pageurl`, `api_server` |
| `textcaptcha` | `text_captcha` | `textcaptcha` |
| `base64` | `image_to_text` | `body` |
| `rotatecaptcha` | `rotate` | `body` |
| `clickcaptcha` | `click` | `body`, `textinstructions` |
| `coordinates` | `coordinates` | `body`, `textinstructions` |
| `grid` | `classification_grid` | `body`, `rows`, `cols`, `textinstructions` |
| `amazon_waf` | `awswaf` | `sitekey`, `iv`, `context` либо `data` (JSON), `pageurl` |
| `audio` | `audio_captcha` | `body` |
| `friendly` / `friendly_captcha` | `friendly_captcha` | `sitekey`, `pageurl` + поля пазла |
| `altcha` | `altcha` | `challenge` или `challenge_url` / `challenge_json`, `pageurl` |

## Legacy `/res.php`

`action=get` — `id` (taskId). Ответ: `OK|<answer>` либо `CAPCHA_NOT_READY` (ещё
считается) либо `ERROR_CAPTCHA_UNSOLVABLE` и родственные коды.

`action=getbalance` — `OK|<balance>`. `action=reportgood` / `action=reportbad` —
`OK_REPORT_RECORDED`. `action=ping` — `OK`.

Формат `<answer>` зависит от типа: токен, текст, `40` (угол), `128` (offset),
`click:3/8/9` (сетка, ячейки с единицы), `click:83,22;187,39` (клик),
`coordinate:x=39,y=59;...` (координаты), компактный JSON для cookie-движков.

## Форма решения (JSON API)

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

## Кабинет

Отдельный префикс `/account`, чтобы фронтенд или телеграм-бот не смешивали свои
эндпоинты с 2captcha-совместимыми. Аутентификация — тот же `clientKey`, но в заголовке
`X-Client-Key`: в теле он выглядел бы как утечка в логи прокси.

| эндпоинт | что отдаёт |
|---|---|
| `GET /account` | баланс, id аккаунта, хвост ключа (`…0001`), дата регистрации, флаг disabled |
| `GET /account/transactions` | страница леджера: `kind` (`topup`/`charge`/`refund`/`bonus`), `amount`, `balanceAfter`, `taskId`, `createdAt` |
| `POST /account/topup` | создаёт `payment_intent`, отдаёт `paymentUrl` (если провайдер умеет) и `instructions` |
| `POST /account/topup/{id}/confirm` | подтверждает оплату, зачисляет, пишет `topup` в леджер. Требует `X-Admin-Token` |
| `GET /account/stats?days=N` | `totals` (задачи, ready/failed, success_rate, расход), `by_type`, `daily` |

**Леджер.** Каждое изменение баланса пишет строку в `transactions` с `balance_after` —
клиент всегда может ответить, откуда взялось число. Баланс остаётся денормализованной
суммой, источник истины — леджер.

**Идемпотентность платежа.** `confirm` делает условный `UPDATE payment_intents SET`
`status='paid' WHERE status='pending'`. Повторный вызов (реплей вебхука) не найдёт
pending-строку и вернёт `credited: false` с текущим балансом.

**Провайдеры.** Сейчас один — `manual`: создаёт интент и печатает инструкцию, зачисление
делает оператор через `confirm`. Реальный эквайринг (ЮKassa, Stripe, CryptoBot)
добавляется одним классом в `application/payments.py` + `register_provider()`; его вебхук
вызывает тот же `confirm`.

## Pingback (`callbackUrl`)

Если в `createTask` передан `callbackUrl`, после перехода задачи в терминальный статус
воркер POST-ит туда ровно то же тело, что вернул бы `getTaskResult`:
`{status, solution, cost, errorCode, errorDescription}`. Таймаут и число попыток —
`CALLBACK_TIMEOUT_SEC` и `CALLBACK_MAX_ATTEMPTS`. Ошибка доставки не влияет на статус
задачи: клиент всё равно может забрать результат опросом.
