# Runbook: prosopo

## 1. Snapshot

- **captcha type:** `CaptchaType.PROSOPO` (`prosopo`) — Prosopo Procaptcha: виджет создаёт self-custodied SR25519-аккаунт, решает frictionless PoW и подписывает commitment; ответ — `procaptcha-response`.
- **antibot family:** нет. Провайдер `api.prosopo.io`, открытый код `prosopo/captcha`.
- **lane:** `browser` (по каталогу), **mode:** `both` (`solve_http` только эскалирует), `browser=True`.
- **tier:** 3 — `experimental=True`; живой прогон не записан.
- **code:** `solvers/interactive/prosopo/{solver,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `sitekey` | да | алиасы `websiteKey`, `siteKey`, `dapp` |
| `websiteURL` | да | страница виджета; хост сравнивается с хостом после навигации |

```json
{"type": "prosopo", "sitekey": "<dapp>", "websiteURL": "https://target.example/"}
```

## 3. Endpoints / flow

`endpoints.py` держит только провайдерскую поверхность для health/preflight: `/v1/prosopo/provider/status`, `/client/captcha/image`, `/client/solution`, `/siteverify`. Токена по sitekey там нет.

1. `solve_http`: если у контекста есть `browser` — задача решается тут же; иначе `ERR_NEEDS_BROWSER` (подпись commitment-а без ключа пользователя невозможна).
2. `solve_browser`: `open_session` → `navigate` → `wait_js(TOKEN_JS)`, где `TOKEN_JS` ищет `input[name="procaptcha-response"]` (включая shadow DOM), затем `window.procaptchaResponse`, затем `window.procaptcha.getResponse(0)`.
3. Если пусто — `click_first` по `[data-testid="procaptcha-checkbox"]`, `.procaptcha-checkbox`, `#procaptcha`, `.procaptcha` и повторное ожидание (половина бюджета).
4. `read_ua(page)` — UA, которым минтили токен; хост после навигации сравнивается с ожидаемым.

## 4. Solution keys

`SolutionKind.TOKEN` → `token` (= `procaptcha-response`) и `userAgent` из страницы.

```json
{"token": "<procaptcha-response>", "userAgent": "<ua, снятый со страницы>"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_NEEDS_BROWSER` | `the commitment is signed by the widget's SR25519 user` | HTTP-воркер без провайдера | эскалация штатная: ровно один прыжок |
| `ERR_UNSOLVABLE` | `payload missing sitekey/websiteKey`, `payload missing websiteURL` | пустой payload | требовать оба поля |
| `ERR_UNSOLVABLE` | `the page left the expected origin` | редирект на чужой хост | сменить прокси/страницу |
| `ERR_CHALLENGE_EXPIRED` | `prosopo issued no procaptcha-response` | виджет не отдал токен за бюджет | новый раунд, свежий профиль |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_prosopo.py` (эскалация, отсутствие sitekey/URL, чтение токена, смена origin).
- Канареечной фикстуры нет; браузерный смоук — `tests/integration/browser/test_camoufox_local.py` (скипается без camoufox).
- Кейс (не добавлен):

```json
{"name": "prosopo-live", "clientKey": "<canary-key>",
 "task": {"type": "prosopo", "sitekey": "<dapp>", "websiteURL": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. Живого виджета нет: `TOKEN_JS` покрывает три способа чтения, но реальный DOM Prosopo не проверялся.
2. Браузерный лейн требует живой `browser`-heartbeat; тип продаётся только при нём (`CapabilityRegistry.live_types`).
3. Клик по чекбоксу — эвристика: у встраиваемых виджетов селектор может отличаться, тогда раунд = `ERR_CHALLENGE_EXPIRED`.
4. Бюджет — `timeout_s` контекста (`_browser.timeout_s`), а не отдельный knob; на медленном прокси половина бюджета уходит в первое ожидание.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_prosopo.py
uv run ruff check src/captcha/solvers/interactive/prosopo
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/prosopo/solver.py), [endpoints.py](../src/captcha/solvers/interactive/prosopo/endpoints.py), [_browser.py](../src/captcha/solvers/interactive/_browser.py)
- браузерный лейн: [browser.md](browser.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
