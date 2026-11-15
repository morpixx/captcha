# Протокол

Совместимость с JSON API 2captcha (её же копируют capsolver и capmonster) плюс её
form-encoded предшественник. Клиент меняет только base URL и `clientKey`, интеграцию не
переписывает.

## Эндпоинты

~~~
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
~~~

`/admin/*` отвечает 404, если `ADMIN_TOKEN` пуст, и 401 на неверный
`X-Admin-Token`. По умолчанию поверхность выключена.

## Конвенции

1. **HTTP всегда 200.** Бизнес-ошибка приходит как `errorId != 0` + `errorCode` +
   `errorDescription`. Клиентские SDK смотрят только на `errorId`. Реализация:
   `services/gateway/errors.py`.
2. **Payload mode.** Клиент сам собирает sitekey, cookies, свежие sensor-данные. Отдельного
   URL-режима («дай URL — верну cookie») в API нет. Для hCaptcha-семейства браузерный солвер
   сам открывает `websiteURL`: это деталь реализации типа, а не второй режим API.
3. **Один эндпоинт на все типы.** `task.type` решает, кто обработает задачу; остальные
   параметры уходят в `payload` как есть.
4. **Лейн выбирает тип, не клиент.** `http` / `cv` / `protocol` / `browser` заданы в
   `domain/catalog.py`. Клиент не может попросить браузер.

## Имена типов

`type` принимает три вида записи, все три канонизируются в `CaptchaType`
(`services/gateway/type_map.py`):

1. официальное имя задачи 2captcha/capsolver — `HCaptchaTaskProxyless`;
2. наше snake_case-имя — `hcaptcha`;
3. любое из перечисленных с другим регистром и разделителями — `HCaptchaTaskProxyLess`,
   `hcaptcha_task_proxyless`. Сравнение идёт по ключу без не-алфанумерических символов.

| семейство | официальные имена | внутренний тип |
|---|---|---|
| hCaptcha | `HCaptchaTask`, `HCaptchaTaskProxyless`, `HCaptchaTaskProxyLess` | `hcaptcha` |
| hCaptcha Enterprise | `HCaptchaTaskEnterprise`, `HCaptchaTaskEnterpriseProxyless`, `HCaptchaTaskEnterpriseProxyLess`, `HCaptchaEnterpriseTask`, `HCaptchaEnterpriseTaskProxyless`, `HCaptchaEnterpriseTaskProxyLess` | `hcaptcha_enterprise` |
| reCAPTCHA v2 | `RecaptchaV2Task`, `RecaptchaV2TaskProxyless`, `ReCaptchaV2Task`, `ReCaptchaV2TaskProxyLess` | `recaptcha_v2` |
| reCAPTCHA v2 Enterprise | `RecaptchaV2EnterpriseTask`, `RecaptchaV2EnterpriseTaskProxyless`, `ReCaptchaV2EnterpriseTask`, `ReCaptchaV2EnterpriseTaskProxyLess` | `recaptcha_v2_enterprise` |
| reCAPTCHA v3 | `RecaptchaV3Task`, `RecaptchaV3TaskProxyless`, `ReCaptchaV3Task`, `ReCaptchaV3TaskProxyLess` | `recaptcha_v3` |
| reCAPTCHA v3 Enterprise | `RecaptchaV3EnterpriseTask`, `RecaptchaV3EnterpriseTaskProxyless`, `ReCaptchaV3EnterpriseTask`, `ReCaptchaV3EnterpriseTaskProxyLess` | `recaptcha_v3_enterprise` |
| Cloudflare Turnstile | `TurnstileTask`, `TurnstileTaskProxyless`, `TurnstileTaskProxyLess`, `AntiTurnstileTask`, `AntiTurnstileTaskProxyLess` | `turnstile` |
| FunCaptcha / Arkose | `FunCaptchaTask`, `FunCaptchaTaskProxyless`, `FunCaptchaTaskProxyLess` | `funcaptcha` |
| GeeTest | `GeeTestTask`, `GeeTestTaskProxyless`, `GeeTestTaskProxyLess` | `geetest_v3` |
| картинка → текст | `ImageToTextTask`, `ImageToTextTaskMugshot` | `image_to_text` |

Остальные типы (antibot-движки, interactive-вендоры, GeeTest v4, Turnstile invisible,
slider/rotate/click/bbox, PoW) официальных имён задач не имеют — только snake_case из
`domain/enums.py`. Это не «недоделка»: в 2captcha они идут под своими методами legacy API
или вообще отсутствуют.

Неизвестное имя типа — `ERROR_BAD_REQUEST` (errorId 21) от валидатора, без списания.
Известное имя типа, для которого в этой сборке нет солвера, — `ERROR_TASK_NOT_SUPPORTED`
(errorId 12), тоже без списания.

URL задачи — `websiteURL`/`pageurl`, `challenge_url`, `surl`, `api_server` — проверяются
до списания и до постановки в очередь (`adapters/netguard.py`): только `http`/`https`,
только публичный A/AAAA. Приватный, loopback, link-local, metadata-адрес и
неразрешающийся хост дают `ERROR_BAD_REQUEST` (errorId 21); задача не создаётся и не
списывается. Голый хост в `surl`/`api_server` трактуется как `https://<host>` — ровно так
его собирает солвер. Тот же guard стоит в транспорте: `Client` не открывает сокет на
приватный адрес и проверяет каждый redirect hop, а не только первый URL.

## Прокси

Плоские поля официального API (camelCase, snake_case тоже принимается):

| поле | смысл |
|---|---|
| `proxyType` | схема: `http` (по умолчанию), `https`, `socks4`, `socks5`, `socks4a`, `socks5h` |
| `proxyAddress` | host или host:port; IPv6 — в скобках |
| `proxyPort` | 1–65535; обязателен, если порт не указан в адресе |
| `proxyLogin` | логин, URL-кодируется |
| `proxyPassword` | пароль, URL-кодируется |

Всё это сворачивается в один канонический `scheme://[login:password@]host:port`
(`build_proxy_url`) и удаляется из payload. Правила:

- `proxy` строкой — тоже принимается и **выигрывает** у плоских полей; схема из строки,
  иначе из `proxyType`, иначе `http`. 2captcha-форма `login:password@host:port` без схемы
  и полный URL — оба допустимы; без порта — `ERROR_BAD_REQUEST`.
- `proxyPassword` без `proxyLogin` — ошибка. Логин с пустым паролем — допустим.
- Плоские поля без `proxyAddress` — ошибка (это не proxyless-задача, а битое тело);
  proxyless — это отсутствие всех пяти полей.
- Сообщения об ошибке прокси называют поле и никогда значение: логин/пароль проходят через
  разбор и не должны попасть ни в лог, ни в `errorDescription`.
- Legacy `/in.php` дополнительно понимает `proxytypeaddr` — либо одну схему (`HTTP`),
  либо `HTTP:host:port` (`services/gateway/legacy_map.py`).

## hCaptcha: дополнительные поля

| поле | внутренний ключ | смысл |
|---|---|---|
| `websiteURL` | `site_url` | href страницы, где стоит виджет |
| `websiteKey` | `sitekey` | sitekey |
| `host` | `host` | домен виджета для `checksiteconfig`; по умолчанию берётся из `websiteURL` |
| `isInvisible` / `invisible` | `invisible` (bool) | невидимый виджет; принимает `1/0/true/false/yes/no/on/off` |
| `enterprisePayload` | `enterprise_payload` (object) | объект; JSON-строкой тоже принимается |
| `rqdata` | `rqdata` (str) | строка rqdata; если пришла внутри `enterprisePayload.rqdata`, поднимается наверх |
| `cookies` | `cookies` | три формы: строка `a=b; c=d`, список `{name, value}`, JSON-строка с любым из них |
| `userAgent` | `user_agent` | UA, которым клиент открывал страницу |

`enterprisePayload` строкой, не являющейся JSON, трактуется как голый rqdata-токен — это
встречается у клиентов, которые путают два поля. Всё остальное из `task` проходит в
`payload` без изменений (`extra="allow"`).

## Форма решения (JSON API)

`getTaskResult.solution` собирается по типу капчи в `domain/solution.py`: клиент ищет
конкретный ключ, и универсальное `token` для половины типов не подходит.

| семейство | ключи |
|---|---|
| reCAPTCHA v2/v3 (+enterprise, invisible, callback) | `gRecaptchaResponse`, `userAgent` |
| **hCaptcha (+enterprise, http и browser)** | `gRecaptchaResponse`, `respKey`, `userAgent` |
| Turnstile, FunCaptcha, MTCaptcha, Prosopo, Yidun, Binance, VK, Yandex, CaptchaFox, Cutcaptcha, KeyCaptcha, Capy, Lemin, atb, Twitch, BotGuard, BotDeflector, CyberSiARA | `token`, `userAgent` |
| GeeTest v3 | `challenge`, `validate`, `seccode` |
| GeeTest v4 | `captcha_id`, `lot_number`, `pass_token`, `gen_time`, `captcha_output` |
| Cloudflare Challenge | `cf_clearance`, `cookies`, `token`, `userAgent` |
| DataDome, Akamai, Kasada, PerimeterX, TSPD, Temu | `cookies` + `domains: {host: {cookies}}`, `userAgent` |
| Imperva | то же + `utmvc` |
| Friendly, Altcha, Hunt, Castle, Basilisk, Alibaba, Tencent | вложенный `data` (+ `headers` с UA) |
| AWS WAF | `captcha_voucher` + `existing_token`, либо `cookies["aws-waf-token"]` |
| text / math / image-to-text / audio | `text` |

hCaptcha — единственное семейство, где `gRecaptchaResponse` обязателен: 2captcha-клиент
читает именно его, а не `token`. `respKey` присутствует, только если его вернул виджет
(enterprise и часть sitekey); `userAgent` — UA, с которым токен был получен: клиент обязан
подставить его при сабмите, иначе токен не примут.

Абстрактная геометрия — наши собственные ключи, единого 2captcha-стандарта нет:
`objects` (grid), `coordinates` (click/draw/drag/Shein), `bbox`, `angle`, `offset`.

`SolverResult.extra` всегда побеждает: солвер, который знает свой движок, кладёт
vendor-exact поля, и они проходят насквозь.

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
| `ERR_URL_REJECTED` | 21 | `ERROR_BAD_REQUEST` |
| неверный ключ | 1 | `ERROR_KEY_DOES_NOT_EXIST` / `ERROR_WRONG_USER_KEY` |
| битый запрос | 21 | `ERROR_BAD_REQUEST` |
| квота/лимит (см. ниже) | 2 | `ERROR_NO_SLOT_AVAILABLE` |

Legacy добавляет два своих: `ERROR_NO_SUCH_METHOD` (неизвестный `method`) и
`ERROR_BAD_PARAMETERS` (не хватает поля).

**`ERROR_NO_SLOT_AVAILABLE`** (errorId 2) — сервер сейчас не берёт задачу. Сюда попадают
все четыре лимита аккаунта: `RATE_LIMIT_CREATE_PER_MIN`, `MAX_ACTIVE_TASKS_PER_ACCOUNT`,
`MAX_DAILY_TASKS`, `MAX_DAILY_SPEND` (`services/gateway/quotas.py`), а также legacy
`/in.php`. Задача не создаётся и не списывается; клиент повторяет позже. Это не ошибка
данных: `ERROR_BAD_REQUEST` на то же тело не придёт.

`ERR_NEEDS_BROWSER`, `ERR_QUOTA`, `ERR_URL_REJECTED` — внутренние коды. Наружу они не
уходят (см. следующий раздел); `ERR_QUOTA` пока никем не выбрасывается — лимиты отвечают
envelope-ом `ERROR_NO_SLOT_AVAILABLE` прямо в gateway. `ERR_URL_REJECTED` отвечает клиенту
публичным `ERROR_BAD_REQUEST` (errorId 21) и никогда не доходит до очереди.

## Браузерный лейн в API

Новых эндпоинтов и полей нет. Для клиента задача hCaptcha выглядит так же; меняется только
время ответа и то, кто её решает:

- `hcaptcha` и `hcaptcha_enterprise` по каталогу живут в лейне `browser`; там же
  `prosopo`, `captchafox` и `yandex` — их токен собирается внутри виджета и HTTP-пути
  у него нет, `solve_http` для них сразу поднимает `ERR_NEEDS_BROWSER`;
- HTTP-солвер, упёршийся в отпечаток, бросает `ERR_NEEDS_BROWSER`, воркер перекладывает
  задачу в браузерный лейн и ack-ает исходное сообщение. Клиент этого не видит: статус
  остаётся `processing`, списание не меняется;
- второй прыжок запрещён (`MAX_LANE_HOPS = 1`): повторный `ERR_NEEDS_BROWSER` уже в
  браузерном лейне превращается в `ERR_UNSOLVABLE`;
- бюджет времени — hard timeout 180–300 с (по умолчанию 240). Клиент, который опрашивает
  `getTaskResult` меньше 300 с, может не дождаться ответа браузерного типа.

Живой замер HTTP-пути (снимок 2026-11-15, [hcaptcha-sites.md](hcaptcha-sites.md)):
Steam и Riot на одном egress IP дали **0 pass из 10** каждый (все ответы — новый `c`
вместо токена), контрольный passive-ключ hCaptcha — **10 из 10**. То есть для Steam/Riot
HTTP-лейн не даёт токен в принципе, и клиент hCaptcha-family получает ответ из браузерного
лейна; бюджет времени там 180–300 с. Epic на публичном входе hCaptcha не показывает
(Talon), sitekey не снят — тип для этого хоста не продаётся как решённый.

Passive-ключи (`checksiteconfig` → `{"pass": true}` без `c`) — легальный случай: такой
сайт решается вообще без JS, пустым `n`. `wire.checksite_config` сейчас считает это
`UnsolvableError`; это известный баг HTTP-солвера, а не свойство протокола.

**Внутренние ошибки браузерного лейна не выставляются наружу.** `ERR_NEEDS_BROWSER` не
возвращается никогда; причины внутри лейна (таймаут запуска, kill дерева процессов,
отсутствие слота) остаются в логах и метриках (`captcha_browser_*`,
`captcha_escalations_total`), а клиент видит либо `ready`, либо один из публичных кодов
таблицы выше.

**Известное расхождение.** `getTaskResult` для `status=failed` сейчас кладёт в
`errorCode` внутренний код (`ERR_UNSOLVABLE`, `ERR_TIMEOUT`, …), а не публичный
(`ERROR_CAPTCHA_UNSOLVABLE`): `routes/tasks.py` берёт `env.error["code"]` как есть. То же
в `callbackUrl` (`application/pingback.py`). `createTask` и legacy `/res.php` отдают
публичные коды корректно. Строгий 2captcha-клиент, который сравнивает `errorCode` со
строкой, на этом сломается; чинить — `services/gateway/errors.py` + `routes/tasks.py`
(владелец api-compat/security), не документацией.

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
задачи: клиент всё равно может забрать результат опросом. Код ошибки в callback — тот же
внутренний, что и в `getTaskResult` (см. «Известное расхождение»).

## Тайминги и лимиты

| что | значение | где |
|---|---|---|
| TTL состояния задачи в Redis | 24 ч | `state_store.py` |
| внешний дедлайн задачи | 300 с от создания | `routes/tasks.py`, `routes/legacy.py` |
| hard timeout браузерной задачи | 180–300 с, дефолт 240 | `worker_browser/limits.py` |
| rate limit create | 60/мин на аккаунт (0 = выключен) | `RATE_LIMIT_CREATE_PER_MIN` |
| активных задач | 100 на аккаунт | `MAX_ACTIVE_TASKS_PER_ACCOUNT` |
| задач в сутки | 10 000 на аккаунт | `MAX_DAILY_TASKS` |
| расход в сутки | 50.0 | `MAX_DAILY_SPEND` |