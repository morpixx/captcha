# Runbook: hCaptcha

> Тип, у которого два пути: HTTP (checksiteconfig → n-token → getcaptcha) и браузер
> (реальный виджет). Оба живут в `solvers/hcaptcha/`; лейн по каталогу — `browser`,
> HTTP-путь доступен через внутренний `preferred_mode` и через эскалацию.

## 1. Snapshot

- **captcha type:** `CaptchaType.HCAPTCHA`, `CaptchaType.HCAPTCHA_ENTERPRISE` —
  invisible/visible виджет hCaptcha, ответ — pass-токен `P1_…`.
- **antibot family:** hCaptcha (Intuition Machines). Не путать с grid-классификатором
  `solvers/classification/hcaptcha.py` — это отдельный тип `classification_grid`.
- **mode:** `both` — `HCaptchaHTTPSolver` (`http=True`) и `HCaptchaBrowserSolver`
  (`browser=True`); лейн по каталогу `browser`.
- **current success rate target:** L3 ≥ 0.95 canary pass, coverage ≥ 0.90
  (`docs/plan.md` §2.4). `experimental=True` у обоих солверов; L3 не пройден.
- **живой HTTP-замер (2026-11-15):** Steam и Riot — 0 pass из 10 на каждом хосте (все ответы
  challenge), контрольный passive-ключ hCaptcha — 10/10. Матрица и ограничения:
  [hcaptcha-sites.md](../docs/hcaptcha-sites.md).
- **tier:** 2 (beta): код и протокол есть, L3 нет.
- **owner:** hcaptcha-http (HTTP + `adapters/hcaptcha_js`) и hcaptcha-browser (браузер +
  `sites.py`), по контракту §11.

## 2. Payload shape

Минимум:

~~~json
{
  "type": "HCaptchaTaskProxyless",
  "websiteURL": "https://authenticate.riotgames.com",
  "websiteKey": "<sitekey>"
}
~~~

Полный набор для hCaptcha (см. `docs/protocol.md` «hCaptcha: дополнительные поля»):

| поле | обязательное | что делает солвер |
|---|---|---|
| `websiteURL` | да | href страницы; из него же берётся `host`, если `host` не задан |
| `websiteKey` | да | sitekey; без него `UnsolvableError`. Steam: `e18a349a-46c2-46a0-87a8-74be79345c92` (снят 2026-11-15); Riot — в `sites.py`; Epic — нет в публичных артефактах |
| `host` | нет | домен для `checksiteconfig`; переопределяет вычисленный из URL |
| `isInvisible` / `invisible` | нет | профиль браузера: invisible или обычный виджет |
| `rqdata` / `enterprisePayload` | нет | enterprise-данные; идут в query `checksiteconfig` и в тело `getcaptcha` |
| `cookies` | нет | cookie-jar в браузерную сессию; в HTTP-путь не идут |
| `userAgent` | нет | UA, которым клиент открывал страницу; для HTTP — заголовок, для browser — `BrowserSpec` (Camoufox решает сам) |
| `proxy` / `proxyType…` | нет | прокси задачи; для browser обязателен exit-IP (GeoIP) |
| `vm_data` / `vmData` | нет | сериализованный фингерпринт для n-token; без него bundle видит `null` |
| `client_version` | нет | build-id для `checksiteconfig.v`; дефолт — `wire.DEFAULT_VERSION` |

## 3. Flow

**HTTP (`solvers/hcaptcha/http.py` + `wire.py`):**

1. `params_from_task`: sitekey, host (из `host` или `websiteURL`), href, rqdata, UA;
2. `POST https://api.hcaptcha.com/checksiteconfig?v=<build>&host=&sitekey=&sc=1&swa=1&spst=1`
   → `{c: {type: hsw|hsj, req: <jwt>}, features, pass}`;
3. n-token: bundle из `adapters/hcaptcha_js` (Node + jsdom + canvas) исполняет `hsw(req, …)`;
4. `POST https://api.hcaptcha.com/getcaptcha/<sitekey>` — форма либо msgpack с `Ext(18)`,
   если `features.enc_get_req`; тело: `v/sitekey/host/hl/motionData/n/c/pst`;
5. `P1_`-токен → `SolverResult(token, extra={userAgent, respKey?})`;
6. ответ с картинкой-челленджем **или** отклонённый n-token → `NeedsBrowserError`
   (один прыжок в браузер, без ретрая в HTTP-лейне);
7. HTTP-ошибка/недекодируемое тело → `SensorError` (обычный retry/DLQ).

**Passive-ключи.** `checksiteconfig` может ответить `{"pass": true}` без `c` (тестовый
ключ, пассивные деплойменты). Такой сайт решается без JS: `getcaptcha` формой с пустым `n`
отдаёт pass UUID за ~150 мс. Сейчас `wire.checksite_config` бросает на этом
`UnsolvableError` — известный баг, отдельная задача hcaptcha-http (probe: контроль 10/10).

**Browser (`solvers/hcaptcha/browser.py`):**

1. `resolve_site`: профиль по host (Riot/Steam/Epic/unknown) + поля задачи
   (sitekey, rqdata, enterprise, invisible); `mint_url` из payload не переопределяется;
2. `ctx.browser.session(spec)` — одна свежая Camoufox-сессия, GeoIP по exit-IP прокси;
3. `goto(websiteURL)`, дождаться виджета, `drive_widget` / `click_checkbox`;
4. прочитать токен, `validate_token` (форма `P1_`), проверить, что страница не ушла с
   ожидаемого origin;
5. `SolverResult(token, extra={userAgent, respKey?}, solve_mode=BROWSER)`;
6. нет токена / виджет не ответил → `ChallengeExpiredError` (retryable);
   сменилась форма токена или origin → `UnsolvableError` (терминал).

## 4. Sensor / fingerprint

- **HTTP:** n-token — это не шифртекст, а сериализованный фингерпринт, посчитанный bundle
  hCaptcha. Код: `adapters/hcaptcha_js/` (`server.js`, `bundle.js`, `realm.js`).
  Realm — jsdom + canvas, **не Chrome**: нет реального WebGL, шрифтов Chrome и
  anti-detect-коллектора. На «твёрдых» sitekey (Riot) n-token протокольно верный и всё равно
  отклоняется — это работа браузерного лейна, а не повод ретраить HTTP.
- Свежесть: build-id в `wire.DEFAULT_VERSION`, имя bundle и путь ассета — claims `n`/`l`
  JWT. Меняются с релизом hCaptcha; кэш bundle на диске 12 ч (`bundle.js`), realm-ы
  переиспользуются по build+fingerprint (максимум 4).
- Установка: `cd src/captcha/adapters/hcaptcha_js && npm ci` (node_modules не вендорится);
  без Node HTTP-солвер падает `ConfigError`.
- **Browser:** фингерпринт целиком на Camoufox (`os="windows"`, `geoip=<exit_ip>`,
  `block_webrtc=True`, `enable_cache=False`, без UBO); см. `runbooks/browser.md`.
- Fingerprint store: `fp:<engine>:<ua_hash>` используется в antibot-движках; hCaptcha-путь им
  не пользуется.

## 5. Model / dataset

- HTTP/browser-солверы hCaptcha **не используют ML**: n-token считает JS, токен выдаёт виджет.
- Если сервер вернул картинку-челлендж, HTTP-путь её не решает — эскалация в браузер.
  Grid-голова (`classification/hcaptcha.py`, артефакт `hcaptcha-v1`) относится к типу
  `classification_grid`, не к `hcaptcha`.
- `vm_data`-коллектор: своего нет. Если появится — передавать через payload/bundle.

## 6. Known failure modes

| код / симптом | что значит | типичная причина | что делать |
|---|---|---|---|
| `ERR_NEEDS_BROWSER` в логах HTTP-воркера | HTTP-путь отказал, задача ушла в браузер | sitekey требует реального фингерпринта (Riot) | норма для Riot; следить за `captcha_escalations_total` |
| `ERR_UNSOLVABLE` после эскалации | браузер тоже не дал токен | виджет не открылся, origin сменился, форма токена другая | §7, живой прогон с видимым браузером |
| `ERR_SENSOR` | checksiteconfig/getcaptcha не 200 или не декодируется | сменился build, `v` устарел, прокси режет | обновить `DEFAULT_VERSION`, проверить прокси |
| `ERR_JS_RUNTIME` | bundle не поднялся | нет `npm ci`, нет Node, bundle-URL недоступен | §8 |
| `ERR_CONFIG` «JS bundle runner missing» | воркер не подключил `ctx.extra["hcaptcha_js"]` | wiring worker_http | проверить `context.py`/wiring |
| `ERR_CONFIG` «site profile forbids the browser lane» | профиль сайта `mode=HTTP` | Riot-профиль по ошибке перевели в HTTP | вернуть `SolveMode.BROWSER` в `sites.py` |
| `ERR_PROXY` «could not resolve the proxy exit IP» | GeoIP не смог по прокси | мёртвый прокси, DNS | сменить прокси; host-fallback запрещён осознанно |
| `ERR_TIMEOUT` на 240 с | виджет не ответил в бюджет | тяжёлая страница, медленный прокси | смотреть `captcha_browser_launch_seconds` |
| 0 pass из 10, все ответы — новый `c` | сайт не отдаёт pass без интерактивного ответа | Riot `andint`, Steam `enc_get_req` | это browser-кейс, не баг HTTP-солвера (`hcaptcha-sites.md`) |
| `UnsolvableError` на `{"pass": true}` | passive-ключ, `c` отсутствует | ключ пассивный (тестовый/деплой-контроль) | временно: браузерный лейн; правильно: пустой `n` в `getcaptcha` |
| `JSRuntimeError: bundle worker returned non-JSON` | bundle пишет в stdout, протокол stdio ломается | в `realm.js` нет `virtualConsole`, Steam-бандл логирует `undefined` | известный баг `adapters/hcaptcha_js`; probe отбрасывает не-JSON строки и считает их |
| токен есть, сайт его не принял | `userAgent`/cookies не те | клиент подставил другой UA | клиент обязан слать `userAgent` из решения |

## 7. How to debug

- Фикстуры: `tests/solvers/hcaptcha/fixtures/` — `checksiteconfig_plain.json`,
  `checksiteconfig_hsw.json`, `getcaptcha_pass.json`, `getcaptcha_challenge.json`,
  `getcaptcha_error.json`.
- Тесты: `tests/solvers/hcaptcha/` (`test_http.py`, `test_browser.py`); для HTTP — подмена
  `ctx.http` и `ctx.extra["hcaptcha_js"]`, для browser — фейковый провайдер.
- Живой HTTP-прогон: `make smoke KEY=<clientKey> TYPE=hcaptcha` (нужен Node + `npm ci`).
- Живой browser-прогон: `WORKER_BROWSER_HEADLESS=0` + `--no-headless`, один consumer —
  видно, что происходит на странице.
- Логи: structlog JSON, `hcaptcha.http.pass sitekey=… host=… latency_ms=…`; ошибки не
  содержат токенов.
- Метрики: `captcha_solver_errors_total{type="hcaptcha",error_code}`,
  `captcha_escalations_total{type="hcaptcha"}`, `captcha_task_latency_seconds{solve_mode}`.
- Env: `HCAPTCHA_JS_DEBUG=1` — полные стеки bundle-воркера на stderr.
- **Матрица по сайтам:** `tools/hcaptcha_probe.py` — `probe --runs 10` (все известные
  хосты), `probe --site host=sitekey`, `discover --url <page>` (ищет api.js и sitekey в
  HTML/скриптах). Нужен Node и зависимости бандла:
  `cd src/captcha/adapters/hcaptcha_js && npm ci`.

## 8. How to regenerate

- **Bundle/версия.** Обновить `wire.DEFAULT_VERSION` (build-id из api.js) и, если сменились
  claims, проверить allowlist `*.hcaptcha.com` в `bundle.js`. После правки — replay
  `getcaptcha_*.json` и один живой прогон.
- **Профиль сайта.** Новый sitekey — строка `Site(...)` в `solvers/hcaptcha/sites.py`;
  sitekey не угадывается: пока не снят, поле остаётся `None` с `FILL`-маркером.
  Steam-ключ уже снят (`e18a349a-…`), но в `sites.py` пока `None` — обновление за
  владельцем hcaptcha-browser. Epic: sitekey нет в публичных артефактах (Talon), остаётся `None`.
- **Зависимости.** `npm ci` в `adapters/hcaptcha_js` после обновления `package.json`;
  без него `ERR_JS_RUNTIME`.
- После регена: bump `SolverInfo.version`, прогнать `tests/solvers/hcaptcha`, обновить §6
  и §9 этого файла.

## 9. Incident log

- `2026-11-15 — live probe — 50 HTTP-прогонов (5 хостов × 10): Steam 0/10, Riot 0/10,
  контрольный passive-ключ 10/10; Epic — hCaptcha не найдена (Talon). Вердикт: Steam/Riot —
  browser; HTTP-путь с jsdom-отпечатком не даёт токен. Детали: docs/hcaptcha-sites.md.`
- `2026-11-15 — initial setup — runbook создан; L3 не пройден, живые проценты не измерены.
  Riot: HTTP отклоняется by design, браузер работает; Steam/Epic: sitekey не снят, HTTP-агент
  в работе (`docs/plan.md` §5).`

## 10. References

- hCaptcha docs: <https://docs.hcaptcha.com/>
- 2captcha hCaptcha: <https://2captcha.com/2captcha-api#hcaptcha>
- Код: `src/captcha/solvers/hcaptcha/{http,browser,wire,common,sites}.py`,
  `src/captcha/adapters/hcaptcha_js/README.md`
- Живая матрица сайтов: [hcaptcha-sites.md](../docs/hcaptcha-sites.md), инструмент — `tools/hcaptcha_probe.py`
- Контракт: `docs/production-contract.md` §2, §4, §5; протокол — `docs/protocol.md`
- Браузерная эксплуатация: `runbooks/browser.md`; приёмка — `docs/plan.md` §2