# Runbook: cybersiara

## 1. Snapshot

- **captcha type:** `CaptchaType.CYBERSIARA` (`cybersiara`) — CyberSiARA: слайд-виджет, ответ — один master URL token.
- **antibot family:** `AntibotEngine.CYBERSIARA` (builder — `engines/cybersiara/builder.py`).
- **lane:** `http`, **mode:** `both`, `needs_js_runtime=True`.
- **tier:** 3 — `experimental=True`; тело verify не публично, живого прогона нет.
- **code:** `solvers/antibot/cybersiara/{solver,endpoints,payload,page}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `sitekey` | да | он же `SlideMasterUrlId` / `MasterUrlId` / `master_url_id` |
| `websiteURL` / `pageurl` / `url` | да | страница виджета |
| `master_url_token` / `MasterUrlToken` / `token` | нет | готовый токен: валидируется и возвращается без пере-минта |
| `challenge_js` / `cybersiara_js` / `widget_js` / `script` | нет | исходник виджета для capture-пути |
| `cookies` | нет | jar прошлого раунда |
| `userAgent` / `ua` | нет | UA сессии |
| `timeout_s` | нет | бюджет браузерного ожидания, по умолчанию 20 |

```json
{"type": "cybersiara", "sitekey": "<SlideMasterUrlId>", "websiteURL": "https://target.example/"}
```

## 3. Endpoints / flow

`endpoints.py`: API `https://mfpapi.cybersiara.com`, verify `/api/v1/verify/<sitekey>`; токен ищется под именами `master_url_token`, `MasterUrlToken`, `masterUrlToken`, `master_url`, `token`; cookie-префиксы `master_url`, `cybersiara`, `MasterUrl`.

1. **Pass-through:** готовый токен → `page.validate_token` → возврат.
2. **Capture:** билдер собирает программу, `_runtime.run_script` исполняет виджет; токен берётся из storage/cookies, затем из тел перехваченных запросов, затем из **реплея** verify-вызова (`_replay`) — только на сайт или на `cybersiara.com`.
3. **Browser lane:** навигация, затем цикл `page.harvest` (storage/cookie/global) до появления токена или `timeout_s`.

Тело verify не публично; «чистый HTTP» без скрипта был бы догадкой, поэтому его нет.

## 4. Solution keys

`SolutionKind.TOKEN` → `token`, `userAgent`. `extra`: `userAgent`, `headers: {"User-Agent": ua}`.

```json
{"token": "<master_url_token>", "userAgent": "<ua>"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `requires the site key`, `needs the page URL` | нет sitekey/URL | требовать оба поля |
| `ERR_NEEDS_BROWSER` | `the token is minted by the widget script` | нет скрипта/рантайма | прислать `challenge_js` или браузерный лейн |
| `ERR_CHALLENGE_EXPIRED` | `the page left no token` / `no token in storage, cookies or captured requests` | виджет не дошёл до токена | новый раунд, свежий профиль |
| `ERR_UNSOLVABLE` | `builder returned no capture program`, `validate_token`-отказ | битая программа/токен-заглушка | сверить скрипт и sitekey |
| `ERR_CONFIG` | `SolverContext missing required fields` | воркер без sensors | см. §7 |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/antibot/test_antibot_solvers.py` (CyberSiARA: pass-through, нет sitekey, capture, реплей, отсутствие токена), сборка — `tests/engines/test_engine_builders.py`.
- Живых дампов виджета нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "cybersiara-live", "clientKey": "<canary-key>",
 "task": {"type": "cybersiara", "sitekey": "<SlideMasterUrlId>", "websiteURL": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. Схема verify-вызова не публична: capture/реплей работает по тому, что виджет сам сделал. Pass rate не измерен.
2. Реплей разрешён только на сайт и `cybersiara.com`; виджет, который ходит на отдельный CDN, даст `ERR_CHALLENGE_EXPIRED`.
3. Браузерный лейн harvest-ит storage/cookie/global с интервалом 0.5 с; медленный слайд-интерстишиал может не уложиться в `timeout_s`.
4. `page.validate_token` проверяет форму (sitekey в токене), а не подпись: подделка пройдёт как «решено».
5. Классический слайд требует человекообразного трека; браузерный лейн его не «рисует», а полагается на виджет — на живом тенанте это узкое место.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_antibot_solvers.py tests/engines/test_engine_builders.py
uv run ruff check src/captcha/solvers/antibot/cybersiara src/captcha/engines/cybersiara
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/cybersiara/solver.py), [payload.py](../src/captcha/solvers/antibot/cybersiara/payload.py), [page.py](../src/captcha/solvers/antibot/cybersiara/page.py), [endpoints.py](../src/captcha/solvers/antibot/cybersiara/endpoints.py)
- builder: [builder.py](../src/captcha/engines/cybersiara/builder.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
