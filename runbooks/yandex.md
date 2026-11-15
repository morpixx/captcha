# Runbook: yandex

## 1. Snapshot

- **captcha type:** `CaptchaType.YANDEX` (`yandex`) — Yandex SmartCaptcha: iframe-RPC между `checkbox.*`, `advanced.*`, `backend.*` документами, токен в `input[name="smart-token"]`.
- **antibot family:** нет. Виджет `smartcaptcha.yandexcloud.net/captcha.js`, origin `captcha-api.yandex.ru`.
- **lane:** `browser` (по каталогу), **mode:** `both` (`solve_http` эскалирует), `browser=True`.
- **tier:** 3 — `experimental=True`; живого прогона нет.
- **code:** `solvers/interactive/yandex/{solver,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `sitekey` | да | алиасы `websiteKey`, `siteKey` |
| `websiteURL` | да | страница; хост после навигации сверяется с ним |

```json
{"type": "yandex", "sitekey": "<sitekey>", "websiteURL": "https://target.example/"}
```

## 3. Endpoints / flow

`endpoints.py` держит только стабильные константы загрузчика (`WIDGET`, `ORIGIN`, `ORIGIN_ALT`): хешированные имена iframe-документов меняются с каждой сборкой, HTTP-вызова, минящего токен, нет.

1. `solve_http`: есть `ctx.browser` — решает тут же; иначе `ERR_NEEDS_BROWSER` с sitekey и URL в контексте.
2. `solve_browser`: `open_session` → `navigate` → `wait_js(TOKEN_JS)`; `TOKEN_JS` читает `input[name="smart-token"]`, затем `window.smartCaptcha.getResponse(0)` и `getResponse()`.
3. Если пусто: `window.smartCaptcha.execute(0)` (invisible-виджет игнорирует клик), затем `click_first` по `#js-captcha-checkbox`, `[data-testid="captcha-checkbox"]`, `.SmartCaptcha-Checkbox`, `iframe[data-testid="checkbox-iframe"]`, `.smart-captcha iframe`, и второе ожидание.
4. `read_ua(page)`; хост страницы обязан совпасть с хостом `websiteURL`, иначе `ERR_UNSOLVABLE`.

## 4. Solution keys

`SolutionKind.TOKEN` → `token` (= `smart-token`) и `userAgent` со страницы.

```json
{"token": "<smart-token>", "userAgent": "<ua>"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_NEEDS_BROWSER` | `the smart-token is minted by the widget's iframe/RPC and backend document` | HTTP-воркер без браузера | штатная эскалация |
| `ERR_UNSOLVABLE` | `payload missing sitekey/websiteKey`, `payload missing websiteURL`, `the page left the expected origin` | пустой payload / редирект | требовать поля, сменить прокси |
| `ERR_CHALLENGE_EXPIRED` | `yandex issued no smart-token` | advanced-полоса не решилась | новый раунд, другой профиль/IP |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_yandex.py` (эскалация, чтение токена, клик, origin).
- Канареечной фикстуры нет; браузерный смоук — `tests/integration/browser/test_camoufox_local.py` (без camoufox скипается).
- Кейс (не добавлен):

```json
{"name": "yandex-live", "clientKey": "<canary-key>",
 "task": {"type": "yandex", "sitekey": "<sitekey>", "websiteURL": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. Advanced-полоса (картинки) не решается: солвер только кликает чекбокс и ждёт invisible-полосу. Если сайт требует картинки — `ERR_CHALLENGE_EXPIRED`.
2. Селекторы чекбокса — эвристика; на кастомной вёрстке (`.smart-captcha iframe`) клик может уйти в пустоту.
3. Токен живёт недолго и привязан к сессии: клиент обязан подставить `userAgent` из ответа.
4. Браузерный лейн требует свежего `browser`-heartbeat — без него тип не продаётся.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_yandex.py
uv run ruff check src/captcha/solvers/interactive/yandex
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/yandex/solver.py), [endpoints.py](../src/captcha/solvers/interactive/yandex/endpoints.py), [_browser.py](../src/captcha/solvers/interactive/_browser.py)
- браузерный лейн: [browser.md](browser.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
